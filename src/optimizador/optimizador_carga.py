"""Etapa A: selección y asignación de paquetes a posiciones de carga (MILP).

Ver docs/formulacion_matematica.md para el detalle del modelo. Hay dos modos:

- `optimizar()`: maximiza el ingreso total sujeto a la capacidad del avión.
- `optimizar_con_meta()`: el ingreso ya no se maximiza — es un dato externo
  (lo que el área comercial decidió que este vuelo debe facturar). El monto
  objetivo es tanto piso como techo: el modelo selecciona paquetes cuyo
  ingreso se acerque lo más posible a la meta sin pasarse por mucho (ver
  `MARGEN_SUPERIOR_META`), y entre esas combinaciones, la que mejor
  aprovecha el espacio disponible (volumen y peso).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import pulp

from .entidades import Avion, Paquete, PosicionCarga


@dataclass
class ResultadoOptimizacion:
    avion: Avion
    asignacion: dict[str, list[Paquete]]  # posicion_id -> paquetes asignados
    no_asignados: list[Paquete]
    ingreso_total: float
    estado_solver: str
    brazo_resultante_m: float | None
    monto_objetivo_usd: float | None = None
    ingreso_maximo_posible: float | None = None

    @property
    def ingreso_potencial(self) -> float:
        asignados = {p.id for lst in self.asignacion.values() for p in lst}
        return self.ingreso_total + sum(
            p.ingreso_usd for p in self.no_asignados if p.id not in asignados
        )

    @property
    def faltante_para_meta_usd(self) -> float:
        """Cuánto falta para llegar al monto objetivo (0 si no hay meta, o si ya se cumplió)."""
        if self.monto_objetivo_usd is None:
            return 0.0
        return max(0.0, self.monto_objetivo_usd - self.ingreso_total)

    @property
    def cumple_meta(self) -> bool:
        """La meta ahora es piso Y techo (ver `optimizar_con_meta`): el modelo
        deliberadamente no siempre llega al centavo exacto (la granularidad de
        las cajas no lo permite, y tampoco se pasa del margen superior), así
        que "cumple" es estar dentro de `TOLERANCIA_META_RELATIVA` por debajo
        del objetivo — no exigir el 100.00% exacto."""
        if self.monto_objetivo_usd is None or self.monto_objetivo_usd <= 0:
            return True
        return self.ingreso_total >= self.monto_objetivo_usd * (1 - TOLERANCIA_META_RELATIVA)


def repartir_obligatorio_en_fila(
    paquetes: list[Paquete], aviones_restantes: int
) -> tuple[list[Paquete], int, int]:
    """Reparte la carga obligatoria del stock entre los aviones que quedan
    en la fila, en vez de forzarla toda en el que se está optimizando ahora.

    `pct_obligatorio` se aplica sobre TODO el stock de temporada (pensado
    para la fila completa), pero cada corrida de `optimizar()` /
    `optimizar_con_meta()` solo ve el stock remanente de este momento — sin
    este reparto, forzaría el 100% de lo obligatorio que quede en el stock
    sobre el próximo avión que se optimice, lo que puede ser matemáticamente
    infactible si hay más carga obligatoria de la que un solo avión aguanta
    (ver CLAUDE.md, confirmado alcanzable desde la UI con stock grande y
    ≥20% obligatorio).

    Si la carga obligatoria completa no entra en la porción proporcional
    (`ceil(n_obligatorio / aviones_restantes)`) que le toca a este avión, se
    fuerzan solo esas — de mayor densidad de valor primero, mismo criterio
    que usa el resto del sistema para priorizar carga — y el resto queda con
    `obligatorio=False` SOLO para esta corrida: el stock real (fuera de esta
    función) no se toca, así que el próximo avión de la fila las vuelve a
    ver como obligatorias, con `aviones_restantes` uno menos.

    Con `aviones_restantes <= 1` (el comportamiento de antes de que existiera
    esto) se fuerza toda la carga obligatoria, como siempre.

    Devuelve `(paquetes_efectivos, n_forzados, n_pospuestos)`.
    """
    obligatorios = [p for p in paquetes if p.obligatorio]
    if aviones_restantes <= 1 or not obligatorios:
        return paquetes, len(obligatorios), 0

    cupo = math.ceil(len(obligatorios) / aviones_restantes)
    if cupo >= len(obligatorios):
        return paquetes, len(obligatorios), 0

    forzados = {p.id for p in sorted(obligatorios, key=lambda p: -p.densidad_valor)[:cupo]}
    efectivos = [
        p if (p.id in forzados or not p.obligatorio) else replace(p, obligatorio=False)
        for p in paquetes
    ]
    return efectivos, len(forzados), len(obligatorios) - len(forzados)


def _variables_y_restricciones(
    problema: pulp.LpProblem,
    paquetes: list[Paquete],
    avion: Avion,
    factor_seguridad_volumen: float,
) -> dict[tuple[str, str], pulp.LpVariable]:
    """Agrega a `problema` las variables y restricciones (1)-(6) del modelo
    (ver docs/formulacion_matematica.md) — todo lo que NO es la función
    objetivo, que cada modo de optimización define por su cuenta.
    """
    posiciones = avion.posiciones
    x = {
        (paq.id, pos.id): pulp.LpVariable(f"x_{paq.id}_{pos.id}", cat="Binary")
        for paq in paquetes
        for pos in posiciones
    }

    # (1) Cada paquete a lo más a una posición.
    for paq in paquetes:
        total_asignacion = pulp.lpSum(x[(paq.id, pos.id)] for pos in posiciones)
        if paq.obligatorio:
            problema += total_asignacion == 1, f"obligatorio_{paq.id}"
        else:
            problema += total_asignacion <= 1, f"unico_{paq.id}"

    # (2) y (3) Capacidad de peso y volumen por posición.
    for pos in posiciones:
        problema += (
            pulp.lpSum(paq.peso_kg * x[(paq.id, pos.id)] for paq in paquetes) <= pos.peso_max_kg,
            f"peso_max_{pos.id}",
        )
        problema += (
            pulp.lpSum(paq.volumen_m3 * x[(paq.id, pos.id)] for paq in paquetes)
            <= factor_seguridad_volumen * pos.volumen_max_m3,
            f"volumen_max_{pos.id}",
        )

    # (4) Payload máximo del avión.
    problema += (
        pulp.lpSum(
            paq.peso_kg * x[(paq.id, pos.id)] for paq in paquetes for pos in posiciones
        )
        <= avion.peso_max_carga_kg,
        "payload_max_avion",
    )

    # (5) Balance / CG, linealizado (ver docs/formulacion_matematica.md, sección 2).
    peso_total_ponderado = [
        (paq.peso_kg, pos.brazo_m, x[(paq.id, pos.id)]) for paq in paquetes for pos in posiciones
    ]
    problema += (
        pulp.lpSum(
            peso * (brazo - avion.cg_min_m) * var for peso, brazo, var in peso_total_ponderado
        )
        >= 0,
        "cg_min",
    )
    problema += (
        pulp.lpSum(
            peso * (brazo - avion.cg_max_m) * var for peso, brazo, var in peso_total_ponderado
        )
        <= 0,
        "cg_max",
    )

    return x


def _resolver(problema: pulp.LpProblem, tiempo_limite_s: int) -> str:
    # gapRel: se acepta una solución dentro del 2% del óptimo teórico. Para problemas
    # grandes esto evita que CBC gaste el tiempo entero demostrando optimalidad exacta.
    solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=tiempo_limite_s, gapRel=0.02)
    problema.solve(solver)
    return pulp.LpStatus[problema.status]


def _reparar_capacidad_posicion(
    asignados: list[Paquete], pos: PosicionCarga, factor_seguridad_volumen: float
) -> list[Paquete]:
    """Garantiza que lo asignado a una posición respete su peso/volumen real.

    Normalmente esto ya lo asegura el MILP (restricciones (2)-(3)), pero
    cuando CBC no llega a confirmar ninguna solución entera factible dentro
    del tiempo límite (típico con catálogos grandes, miles de variables
    binarias), PuLP igual devuelve valores de la última relajación LP que
    tocó — fraccionarios (p.ej. 0.48, 0.52) — y el umbral ">0.5" usado para
    extraer la asignación los redondea sin que eso siga respetando la
    restricción original. Sin este reparo, `empaquetado_3d` recibiría una
    posición con más peso/volumen "asignado" del que físicamente cabe.

    Se descartan primero los paquetes no obligatorios de menor densidad de
    valor (ingreso/m³); los obligatorios solo se tocan como último recurso,
    para preservar la semántica de "obligatorio" del MILP en la medida de
    lo posible incluso cuando el solver no terminó de resolver.
    """
    restantes = sorted(asignados, key=lambda p: (p.obligatorio, p.densidad_valor))
    vol_max = factor_seguridad_volumen * pos.volumen_max_m3
    while restantes and (
        sum(p.peso_kg for p in restantes) > pos.peso_max_kg + 1e-6
        or sum(p.volumen_m3 for p in restantes) > vol_max + 1e-6
    ):
        restantes.pop(0)
    return restantes


def _extraer_resultado(
    estado: str,
    x: dict[tuple[str, str], pulp.LpVariable],
    paquetes: list[Paquete],
    avion: Avion,
    factor_seguridad_volumen: float,
    monto_objetivo_usd: float | None = None,
    ingreso_maximo_posible: float | None = None,
) -> ResultadoOptimizacion:
    posiciones = avion.posiciones
    asignacion: dict[str, list[Paquete]] = {pos.id: [] for pos in posiciones}
    for paq in paquetes:
        for pos in posiciones:
            var = x[(paq.id, pos.id)]
            if var.value() is not None and var.value() > 0.5:
                asignacion[pos.id].append(paq)
                break

    # Ver docstring de _reparar_capacidad_posicion: necesario cuando el
    # solver no confirmó una solución entera factible (estado != "Optimal").
    # Barato cuando sí la confirmó — el bucle interno no encuentra nada que
    # reparar y no hace nada.
    asignacion = {
        pos.id: _reparar_capacidad_posicion(asignacion[pos.id], pos, factor_seguridad_volumen)
        for pos in posiciones
    }

    ids_asignados = {p.id for lst in asignacion.values() for p in lst}
    no_asignados = [p for p in paquetes if p.id not in ids_asignados]
    ingreso_total = sum(p.ingreso_usd for lst in asignacion.values() for p in lst)

    peso_total = sum(p.peso_kg for lst in asignacion.values() for p in lst)
    momento_total = sum(
        p.peso_kg * pos.brazo_m for pos in posiciones for p in asignacion[pos.id]
    )
    brazo_resultante = momento_total / peso_total if peso_total > 0 else None

    return ResultadoOptimizacion(
        avion=avion,
        asignacion=asignacion,
        no_asignados=no_asignados,
        ingreso_total=ingreso_total,
        estado_solver=estado,
        brazo_resultante_m=brazo_resultante,
        monto_objetivo_usd=monto_objetivo_usd,
        ingreso_maximo_posible=ingreso_maximo_posible,
    )


def optimizar(
    paquetes: list[Paquete],
    avion: Avion,
    factor_seguridad_volumen: float = 0.85,
    tiempo_limite_s: int = 30,
) -> ResultadoOptimizacion:
    """Resuelve el MILP de selección + asignación a pallet para un avión.

    Maximiza el ingreso total sujeto a:
      - cada paquete se asigna a lo más a una posición,
      - capacidad de peso y volumen (con margen de seguridad) por posición,
      - payload máximo del avión,
      - balance / centro de gravedad dentro del rango admisible,
      - paquetes obligatorios deben ir sí o sí.
    """
    problema = pulp.LpProblem("carga_avion_max_ingreso", pulp.LpMaximize)
    x = _variables_y_restricciones(problema, paquetes, avion, factor_seguridad_volumen)

    problema += pulp.lpSum(
        paq.ingreso_usd * x[(paq.id, pos.id)] for paq in paquetes for pos in avion.posiciones
    )

    estado = _resolver(problema, tiempo_limite_s)
    return _extraer_resultado(estado, x, paquetes, avion, factor_seguridad_volumen)


#: Tolerancia relativa sobre el piso de ingreso exigido en la Fase 2. Sin ella,
#: pedirle al solver "ingreso >= prácticamente el óptimo" convierte la simple
#: factibilidad en un problema combinatorio muy difícil (hay que encontrar casi
#: la ÚNICA combinación que logra ese ingreso exacto) y el solver se cuelga sin
#: converger. Es del mismo orden que el `gapRel` del solver, así que no relaja
#: la meta más de lo que la propia tolerancia del solver ya admite. También se
#: usa como margen de "cumple_meta" (ver `ResultadoOptimizacion`): no siempre
#: se puede tocar el centavo exacto por la granularidad de las cajas.
TOLERANCIA_META_RELATIVA = 0.02

#: Cuánto se permite pasar del monto objetivo. Antes la meta era solo un piso
#: (sin techo) y la Fase 2 maximizaba espacio libremente, lo que podía cargar
#: bastante más plata de la pedida con tal de llenar más el avión — feedback
#: explícito del usuario: "si te dije que debes llevar 25.500, no tienes por
#: qué llevar más". Ahora la meta es piso Y techo: el margen es chico a
#: propósito (mismo orden que TOLERANCIA_META_RELATIVA) para no perder la
#: intención de "es tal monto", dejando solo el margen mínimo que la
#: granularidad de las cajas y el gapRel del solver ya exigen.
MARGEN_SUPERIOR_META = 0.02

#: Cuando el monto objetivo NO es alcanzable ni con el margen superior, el
#: piso a exigir en la Fase 2 no puede ser "el máximo posible" a secas — eso
#: cae en la misma zona dura que TOLERANCIA_META_RELATIVA evita, y aquí no hay
#: ningún motivo para insistir en quedar pegado al techo real del avión (la
#: meta ya se perdió de todas formas, y como el techo real queda por debajo
#: de la meta, no hay riesgo de "pasarse" de lo pedido). Se le da un margen
#: bastante más generoso para que la Fase 2 tenga espacio real donde encontrar
#: mejores combinaciones de volumen/peso, sacrificando algo de ingreso a
#: cambio — probado que el ingreso resultante apenas varía entre pedir el
#: 100% o el 0% del máximo como piso, así que perder ese margen no cuesta casi
#: nada de plata y sí gana bastante espacio utilizado.
MARGEN_META_INALCANZABLE = 0.10


def optimizar_con_meta(
    paquetes: list[Paquete],
    avion: Avion,
    monto_objetivo_usd: float,
    factor_seguridad_volumen: float = 0.85,
    tiempo_limite_s: int = 30,
) -> ResultadoOptimizacion:
    """Selecciona paquetes para acercarse a un monto de ingreso ya decidido
    externamente sin pasarse por mucho (no se maximiza el ingreso — la meta
    es piso Y techo), y entre las combinaciones que logran eso, maximiza el
    aprovechamiento del espacio disponible del avión.

    Se resuelve en dos fases:

    1. Se maximiza el ingreso sujeto a que no exceda `monto_objetivo_usd *
       (1 + MARGEN_SUPERIOR_META)` (el "techo"). Si el objetivo es alcanzable,
       esto encuentra el ingreso más cercano posible a la meta sin pasarse
       del margen. Si NO es alcanzable ni con margen, esta restricción no ata
       — el resultado es directamente el ingreso máximo real del avión con
       este stock (se reutiliza tal cual como `ingreso_maximo_posible`, sin
       necesidad de un solve aparte).
    2. Con ese ingreso como referencia (piso `ingreso_techo * (1 - margen)`,
       techo `monto_objetivo_usd * (1 + MARGEN_SUPERIOR_META)`), se maximiza
       una utilización combinada de espacio: `volumen_usado / volumen_total +
       peso_usado / peso_máximo`, en vez de maximizar ingreso — eligiendo,
       entre las combinaciones que logran (casi) el mismo ingreso que la Fase
       1, la que mejor usa el espacio.

    Si la Fase 1 ya está prácticamente pegada a su propio techo, no hay
    margen real para reoptimizar por espacio sin arriesgarse a pasarse de la
    meta — y en ese caso la Fase 2 directamente se salta. Si la meta NO es
    alcanzable, el margen para la Fase 2 es más generoso (ver
    `MARGEN_META_INALCANZABLE`): ya que la meta se perdió de todas formas y el
    techo real del avión queda por debajo de lo pedido, no hay riesgo de
    pasarse — dejar que la Fase 2 sacrifique algo de ingreso por mejor uso del
    espacio no cuesta la intención original de "es tal monto".
    """
    techo_meta = monto_objetivo_usd * (1 + MARGEN_SUPERIOR_META)

    problema1 = pulp.LpProblem("carga_avion_meta_techo", pulp.LpMaximize)
    x1 = _variables_y_restricciones(problema1, paquetes, avion, factor_seguridad_volumen)
    ingreso_expr1 = pulp.lpSum(
        paq.ingreso_usd * x1[(paq.id, pos.id)] for paq in paquetes for pos in avion.posiciones
    )
    problema1 += ingreso_expr1 <= techo_meta, "techo_meta"
    problema1 += ingreso_expr1

    estado1 = _resolver(problema1, tiempo_limite_s)
    if estado1 == "Infeasible":
        # El techo es provablemente incompatible con las restricciones duras
        # (típicamente: hay carga obligatoria cuyo ingreso ya excede el techo
        # por sí sola — no hay forma de respetar el techo sin dejar de
        # embarcar carga obligatoria). A diferencia de "Not Solved" (el
        # solver no terminó a tiempo), acá no hay nada que extraer ni reparar
        # — CBC ya probó que no existe ninguna solución. Cae al resultado sin
        # restricción de techo, que sí es factible (asumiendo que el problema
        # sin meta lo es) — el techo queda sin respetar en este caso de
        # borde, mejor eso que un resultado con el balance del avión roto.
        resultado_sin_techo = optimizar(paquetes, avion, factor_seguridad_volumen, tiempo_limite_s)
        resultado_sin_techo.monto_objetivo_usd = monto_objetivo_usd
        resultado_sin_techo.ingreso_maximo_posible = resultado_sin_techo.ingreso_total
        return resultado_sin_techo

    resultado1 = _extraer_resultado(estado1, x1, paquetes, avion, factor_seguridad_volumen)
    ingreso_techo = resultado1.ingreso_total
    objetivo_alcanzable = ingreso_techo >= monto_objetivo_usd * (1 - TOLERANCIA_META_RELATIVA)

    if ingreso_techo >= techo_meta * (1 - TOLERANCIA_META_RELATIVA):
        resultado1.monto_objetivo_usd = monto_objetivo_usd
        resultado1.ingreso_maximo_posible = ingreso_techo
        return resultado1

    margen = TOLERANCIA_META_RELATIVA if objetivo_alcanzable else MARGEN_META_INALCANZABLE
    piso_ingreso = ingreso_techo * (1 - margen)

    problema2 = pulp.LpProblem("carga_avion_meta_espacio", pulp.LpMaximize)
    x2 = _variables_y_restricciones(problema2, paquetes, avion, factor_seguridad_volumen)
    ingreso_expr2 = pulp.lpSum(
        paq.ingreso_usd * x2[(paq.id, pos.id)] for paq in paquetes for pos in avion.posiciones
    )
    problema2 += ingreso_expr2 >= piso_ingreso, "piso_ingreso"
    problema2 += ingreso_expr2 <= techo_meta, "techo_meta"

    volumen_total_m3 = avion.volumen_total_m3
    peso_total_kg = avion.peso_max_carga_kg
    utilizacion_volumen = pulp.lpSum(
        paq.volumen_m3 * x2[(paq.id, pos.id)] for paq in paquetes for pos in avion.posiciones
    ) / volumen_total_m3
    utilizacion_peso = pulp.lpSum(
        paq.peso_kg * x2[(paq.id, pos.id)] for paq in paquetes for pos in avion.posiciones
    ) / peso_total_kg
    problema2 += utilizacion_volumen + utilizacion_peso

    # La Fase 2 puede caer en una zona dura para el solver (pedirle un piso de
    # ingreso cercano al óptimo lo obliga a acotar casi la única combinación que
    # lo logra). Se le da menos tiempo que a la Fase 1: si no llega a una
    # solución óptima confirmada, mejor no confiar en un incumbente a medio
    # resolver y volver a la solución de la Fase 1, que siempre es válida y ya
    # respeta el piso y el techo.
    estado2 = _resolver(problema2, min(tiempo_limite_s, 15))
    if estado2 != "Optimal":
        resultado1.monto_objetivo_usd = monto_objetivo_usd
        resultado1.ingreso_maximo_posible = ingreso_techo
        return resultado1

    return _extraer_resultado(
        estado2, x2, paquetes, avion, factor_seguridad_volumen,
        monto_objetivo_usd=monto_objetivo_usd,
        ingreso_maximo_posible=ingreso_techo,
    )
