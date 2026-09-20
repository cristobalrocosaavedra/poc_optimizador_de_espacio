"""Etapa A: selección y asignación de paquetes a posiciones de carga (MILP).

Ver docs/formulacion_matematica.md para el detalle del modelo. Hay dos modos:

- `optimizar()`: maximiza el ingreso total sujeto a la capacidad del avión.
- `optimizar_con_meta()`: el ingreso ya no se maximiza — es un dato externo
  (lo que el área comercial decidió que este vuelo debe facturar). El
  modelo selecciona paquetes hasta alcanzar esa meta y, con eso ya
  garantizado, maximiza el aprovechamiento del espacio disponible (volumen
  y peso) entre todas las combinaciones que la cumplen.
"""

from __future__ import annotations

from dataclasses import dataclass

import pulp

from .entidades import Avion, Paquete


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
        return self.monto_objetivo_usd is None or self.faltante_para_meta_usd <= 0.01


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


def _extraer_resultado(
    estado: str,
    x: dict[tuple[str, str], pulp.LpVariable],
    paquetes: list[Paquete],
    avion: Avion,
    monto_objetivo_usd: float | None = None,
    ingreso_maximo_posible: float | None = None,
) -> ResultadoOptimizacion:
    posiciones = avion.posiciones
    asignacion: dict[str, list[Paquete]] = {pos.id: [] for pos in posiciones}
    ids_asignados: set[str] = set()
    for paq in paquetes:
        for pos in posiciones:
            var = x[(paq.id, pos.id)]
            if var.value() is not None and var.value() > 0.5:
                asignacion[pos.id].append(paq)
                ids_asignados.add(paq.id)
                break

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
    return _extraer_resultado(estado, x, paquetes, avion)


#: Tolerancia relativa sobre el piso de ingreso exigido en la Fase 2. Sin ella,
#: pedirle al solver "ingreso >= prácticamente el óptimo" convierte la simple
#: factibilidad en un problema combinatorio muy difícil (hay que encontrar casi
#: la ÚNICA combinación que logra ese ingreso exacto) y el solver se cuelga sin
#: converger. Es del mismo orden que el `gapRel` del solver, así que no relaja
#: la meta más de lo que la propia tolerancia del solver ya admite.
TOLERANCIA_META_RELATIVA = 0.02

#: Cuando el monto objetivo NO es alcanzable, el piso a exigir en la Fase 2 no
#: puede ser "el máximo posible" a secas — eso cae en la misma zona dura que
#: TOLERANCIA_META_RELATIVA evita, y aquí no hay ningún motivo para insistir en
#: quedar pegado al techo (la meta ya se perdió de todas formas). Se le da un
#: margen bastante más generoso para que la Fase 2 tenga espacio real donde
#: encontrar mejores combinaciones de volumen/peso, sacrificando algo de
#: ingreso a cambio — probado que el ingreso resultante apenas varía entre
#: pedir el 100% o el 0% del máximo como piso, así que perder ese margen no
#: cuesta casi nada de plata y sí gana bastante espacio utilizado.
MARGEN_META_INALCANZABLE = 0.10


def optimizar_con_meta(
    paquetes: list[Paquete],
    avion: Avion,
    monto_objetivo_usd: float,
    factor_seguridad_volumen: float = 0.85,
    tiempo_limite_s: int = 30,
) -> ResultadoOptimizacion:
    """Selecciona paquetes para alcanzar un monto de ingreso ya decidido
    externamente (no se maximiza el ingreso), y dentro de eso, maximiza el
    aprovechamiento del espacio disponible del avión.

    Se resuelve en dos fases:

    1. Se calcula el ingreso máximo posible (`optimizar()`), para saber si el
       monto objetivo es alcanzable con la capacidad y el catálogo
       disponibles. El piso de ingreso a exigir es `min(monto_objetivo,
       ingreso_máximo_posible)` — si el objetivo no es alcanzable, se exige
       el máximo posible y se reporta cuánto falta (`faltante_para_meta_usd`).
    2. Con ese piso de ingreso como restricción (con un pequeño margen, ver
       `TOLERANCIA_META_RELATIVA`), se maximiza una utilización combinada de
       espacio: `volumen_usado / volumen_total + peso_usado / peso_máximo`,
       en vez de maximizar ingreso.

    Si la meta ya está prácticamente en el techo de lo alcanzable, no hay
    margen real para reoptimizar por espacio sin sacrificar ingreso — y en
    ese caso la Fase 2 directamente se salta (la mejor combinación de espacio
    ES la de ingreso máximo). Si la meta NO es alcanzable, tampoco tiene
    sentido pedirle al piso que se quede pegado al máximo (ver
    `MARGEN_META_INALCANZABLE`): ya que la meta se perdió de todas formas, se
    le da a la Fase 2 margen real para optimizar espacio en vez de forzarla a
    buscar casi la única combinación que roza el ingreso máximo.
    """
    resultado_maximo = optimizar(paquetes, avion, factor_seguridad_volumen, tiempo_limite_s)
    ingreso_maximo_posible = resultado_maximo.ingreso_total
    objetivo_alcanzable = monto_objetivo_usd <= ingreso_maximo_posible
    piso_ingreso = min(monto_objetivo_usd, ingreso_maximo_posible)

    if objetivo_alcanzable and piso_ingreso >= ingreso_maximo_posible * (1 - TOLERANCIA_META_RELATIVA):
        resultado_maximo.monto_objetivo_usd = monto_objetivo_usd
        resultado_maximo.ingreso_maximo_posible = ingreso_maximo_posible
        return resultado_maximo

    margen = TOLERANCIA_META_RELATIVA if objetivo_alcanzable else MARGEN_META_INALCANZABLE
    piso_con_margen = piso_ingreso * (1 - margen)

    problema = pulp.LpProblem("carga_avion_meta_espacio", pulp.LpMaximize)
    x = _variables_y_restricciones(problema, paquetes, avion, factor_seguridad_volumen)

    problema += (
        pulp.lpSum(
            paq.ingreso_usd * x[(paq.id, pos.id)] for paq in paquetes for pos in avion.posiciones
        )
        >= piso_con_margen,
        "piso_ingreso",
    )

    volumen_total_m3 = avion.volumen_total_m3
    peso_total_kg = avion.peso_max_carga_kg
    utilizacion_volumen = pulp.lpSum(
        paq.volumen_m3 * x[(paq.id, pos.id)] for paq in paquetes for pos in avion.posiciones
    ) / volumen_total_m3
    utilizacion_peso = pulp.lpSum(
        paq.peso_kg * x[(paq.id, pos.id)] for paq in paquetes for pos in avion.posiciones
    ) / peso_total_kg
    problema += utilizacion_volumen + utilizacion_peso

    # La Fase 2 puede caer en una zona dura para el solver (pedirle un piso de
    # ingreso cercano al óptimo lo obliga a acotar casi la única combinación que
    # lo logra). Se le da menos tiempo que a la Fase 1: si no llega a una
    # solución óptima confirmada, mejor no confiar en un incumbente a medio
    # resolver y volver a la solución de ingreso máximo, que siempre es válida
    # y ya cumple el piso.
    estado = _resolver(problema, min(tiempo_limite_s, 15))
    if estado != "Optimal":
        resultado_maximo.monto_objetivo_usd = monto_objetivo_usd
        resultado_maximo.ingreso_maximo_posible = ingreso_maximo_posible
        return resultado_maximo

    return _extraer_resultado(
        estado, x, paquetes, avion,
        monto_objetivo_usd=monto_objetivo_usd,
        ingreso_maximo_posible=ingreso_maximo_posible,
    )
