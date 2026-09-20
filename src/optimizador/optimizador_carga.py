"""Etapa A: selección y asignación de paquetes a posiciones de carga (MILP).

Ver docs/formulacion_matematica.md para el detalle del modelo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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

    @property
    def ingreso_potencial(self) -> float:
        asignados = {p.id for lst in self.asignacion.values() for p in lst}
        return self.ingreso_total + sum(
            p.ingreso_usd for p in self.no_asignados if p.id not in asignados
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
    problema = pulp.LpProblem("carga_avion", pulp.LpMaximize)

    posiciones = avion.posiciones
    x = {
        (paq.id, pos.id): pulp.LpVariable(f"x_{paq.id}_{pos.id}", cat="Binary")
        for paq in paquetes
        for pos in posiciones
    }

    # Objetivo: maximizar ingreso total.
    problema += pulp.lpSum(
        paq.ingreso_usd * x[(paq.id, pos.id)] for paq in paquetes for pos in posiciones
    )

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

    # gapRel: se acepta una solución dentro del 2% del óptimo teórico. Para problemas
    # grandes esto evita que CBC gaste el tiempo entero demostrando optimalidad exacta.
    solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=tiempo_limite_s, gapRel=0.02)
    problema.solve(solver)

    estado = pulp.LpStatus[problema.status]

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
    ingreso_total = sum(
        p.ingreso_usd for lst in asignacion.values() for p in lst
    )

    peso_total = sum(p.peso_kg for lst in asignacion.values() for p in lst)
    momento_total = sum(
        p.peso_kg * pos.brazo_m
        for pos in posiciones
        for p in asignacion[pos.id]
    )
    brazo_resultante = momento_total / peso_total if peso_total > 0 else None

    return ResultadoOptimizacion(
        avion=avion,
        asignacion=asignacion,
        no_asignados=no_asignados,
        ingreso_total=ingreso_total,
        estado_solver=estado,
        brazo_resultante_m=brazo_resultante,
    )
