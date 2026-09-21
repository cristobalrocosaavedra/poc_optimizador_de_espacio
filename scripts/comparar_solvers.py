"""Comparativa empírica de solvers MILP para la Etapa A (selección + asignación),
a distintos tamaños de catálogo — para decidir con datos, no con intuición, si
conviene cambiar de CBC/PuLP (el solver actual) a otra cosa quien el problema
crezca. No cambia nada de producción: es una herramienta de decisión.

Prueba, en el mismo formulario exacto que usa `optimizador_carga.py`
(mismas restricciones: peso/volumen por posición, payload, CG, obligatorios):

  - CBC vía PuLP        (el solver actual en producción)
  - CBC vía OR-Tools     (mismo algoritmo, otro wrapper — separa "es CBC" de
                          "es el wrapper de PuLP")
  - SCIP vía OR-Tools    (solver MIP de propósito general, gratis)
  - HiGHS vía OR-Tools   (solver MIP moderno, gratis, viene bundleado en
                          OR-Tools en este entorno — no hace falta instalar
                          `highspy` aparte)
  - CP-SAT vía OR-Tools  (SAT-based, coeficientes tienen que ser enteros —
                          se escalan peso/volumen/ingreso con precisión de
                          centavos/cm³, ver `_construir_cp_sat`)

Gurobi/CPLEX (pagos) NO se pueden probar en este entorno: no hay librería ni
licencia instalada (`GUROBI`/`XPRESS` no disponibles vía OR-Tools acá). Si
quieres esos números, hay que correr esto con una licencia real — el resto
de la comparativa sigue siendo válida como referencia de qué esperar de
motores gratuitos primero, antes de pagar por uno.

Uso:
    python3 scripts/comparar_solvers.py                  # barrido completo
    python3 scripts/comparar_solvers.py --rapido          # tamaños chicos, para iterar
    python3 scripts/comparar_solvers.py --hilos           # comparativa de threads a n=8000
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pulp  # noqa: E402
from ortools.linear_solver import pywraplp  # noqa: E402
from ortools.sat.python import cp_model  # noqa: E402

from optimizador.datos_simulados import crear_avion, generar_catalogo_paquetes  # noqa: E402
from optimizador.entidades import Avion, Paquete  # noqa: E402

TIEMPO_LIMITE_S = 30
TAMANOS_DEFECTO = [1500, 3000, 5000, 8000, 12000]
TAMANOS_RAPIDOS = [1000, 2500]

# Factor de escala para CP-SAT (necesita coeficientes enteros). 100 preserva
# centavos de ingreso y "centigramos" de peso; el volumen ya es prácticamente
# entero en cm³ porque las dimensiones de las cajas vienen con 1 decimal.
ESCALA_PLATA_PESO = 100
ESCALA_VOLUMEN = 1_000_000


def _catalogo(n: int, seed: int = 42) -> list[Paquete]:
    return generar_catalogo_paquetes(
        n=n, seed=seed, pct_obligatorio=0.10, pct_no_apilable=0.15, pct_riesgo_alto=0.06,
    )


def _resolver_pulp(paquetes: list[Paquete], avion: Avion, factor_seguridad: float, tiempo_s: int, threads: int):
    problema = pulp.LpProblem("bench", pulp.LpMaximize)
    x = {
        (p.id, pos.id): pulp.LpVariable(f"x_{p.id}_{pos.id}", cat="Binary")
        for p in paquetes for pos in avion.posiciones
    }
    for p in paquetes:
        total = pulp.lpSum(x[(p.id, pos.id)] for pos in avion.posiciones)
        problema += (total == 1) if p.obligatorio else (total <= 1)
    for pos in avion.posiciones:
        problema += pulp.lpSum(p.peso_kg * x[(p.id, pos.id)] for p in paquetes) <= pos.peso_max_kg
        problema += pulp.lpSum(p.volumen_m3 * x[(p.id, pos.id)] for p in paquetes) <= factor_seguridad * pos.volumen_max_m3
    problema += pulp.lpSum(p.peso_kg * x[(p.id, pos.id)] for p in paquetes for pos in avion.posiciones) <= avion.peso_max_carga_kg
    mom = [(p.peso_kg, pos.brazo_m, x[(p.id, pos.id)]) for p in paquetes for pos in avion.posiciones]
    problema += pulp.lpSum(peso * (brazo - avion.cg_min_m) * v for peso, brazo, v in mom) >= 0
    problema += pulp.lpSum(peso * (brazo - avion.cg_max_m) * v for peso, brazo, v in mom) <= 0
    problema += pulp.lpSum(p.ingreso_usd * x[(p.id, pos.id)] for p in paquetes for pos in avion.posiciones)

    solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=tiempo_s, gapRel=0.02, threads=threads)
    t0 = time.time()
    problema.solve(solver)
    t = time.time() - t0
    estado = pulp.LpStatus[problema.status]
    objetivo = pulp.value(problema.objective) if estado in ("Optimal", "Not Solved") else None
    return estado, objetivo, t


def _resolver_pywraplp(backend: str, paquetes: list[Paquete], avion: Avion, factor_seguridad: float, tiempo_s: int, threads: int):
    solver = pywraplp.Solver.CreateSolver(backend)
    if solver is None:
        return "NO_DISPONIBLE", None, 0.0
    solver.SetTimeLimit(tiempo_s * 1000)
    try:
        solver.SetNumThreads(threads)
    except Exception:
        pass  # no todos los backends exponen esto

    x = {(p.id, pos.id): solver.BoolVar(f"x_{p.id}_{pos.id}") for p in paquetes for pos in avion.posiciones}
    for p in paquetes:
        total = solver.Sum(x[(p.id, pos.id)] for pos in avion.posiciones)
        solver.Add(total == 1 if p.obligatorio else total <= 1)
    for pos in avion.posiciones:
        solver.Add(solver.Sum(p.peso_kg * x[(p.id, pos.id)] for p in paquetes) <= pos.peso_max_kg)
        solver.Add(solver.Sum(p.volumen_m3 * x[(p.id, pos.id)] for p in paquetes) <= factor_seguridad * pos.volumen_max_m3)
    solver.Add(solver.Sum(p.peso_kg * x[(p.id, pos.id)] for p in paquetes for pos in avion.posiciones) <= avion.peso_max_carga_kg)
    mom = [(p.peso_kg, pos.brazo_m, x[(p.id, pos.id)]) for p in paquetes for pos in avion.posiciones]
    solver.Add(solver.Sum(peso * (brazo - avion.cg_min_m) * v for peso, brazo, v in mom) >= 0)
    solver.Add(solver.Sum(peso * (brazo - avion.cg_max_m) * v for peso, brazo, v in mom) <= 0)
    solver.Maximize(solver.Sum(p.ingreso_usd * x[(p.id, pos.id)] for p in paquetes for pos in avion.posiciones))

    t0 = time.time()
    status = solver.Solve()
    t = time.time() - t0
    nombres = {
        pywraplp.Solver.OPTIMAL: "Optimal", pywraplp.Solver.FEASIBLE: "Feasible",
        pywraplp.Solver.INFEASIBLE: "Infeasible", pywraplp.Solver.NOT_SOLVED: "Not Solved",
        pywraplp.Solver.ABNORMAL: "Abnormal", pywraplp.Solver.UNBOUNDED: "Unbounded",
    }
    estado = nombres.get(status, str(status))
    objetivo = solver.Objective().Value() if status in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE) else None
    return estado, objetivo, t


def _resolver_cp_sat(paquetes: list[Paquete], avion: Avion, factor_seguridad: float, tiempo_s: int, threads: int):
    modelo = cp_model.CpModel()
    x = {(p.id, pos.id): modelo.NewBoolVar(f"x_{p.id}_{pos.id}") for p in paquetes for pos in avion.posiciones}
    for p in paquetes:
        total = sum(x[(p.id, pos.id)] for pos in avion.posiciones)
        if p.obligatorio:
            modelo.Add(total == 1)
        else:
            modelo.Add(total <= 1)
    for pos in avion.posiciones:
        peso_max_i = round(pos.peso_max_kg * ESCALA_PLATA_PESO)
        vol_max_i = round(factor_seguridad * pos.volumen_max_m3 * ESCALA_VOLUMEN)
        modelo.Add(sum(round(p.peso_kg * ESCALA_PLATA_PESO) * x[(p.id, pos.id)] for p in paquetes) <= peso_max_i)
        modelo.Add(sum(round(p.volumen_m3 * ESCALA_VOLUMEN) * x[(p.id, pos.id)] for p in paquetes) <= vol_max_i)
    payload_max_i = round(avion.peso_max_carga_kg * ESCALA_PLATA_PESO)
    modelo.Add(
        sum(round(p.peso_kg * ESCALA_PLATA_PESO) * x[(p.id, pos.id)] for p in paquetes for pos in avion.posiciones)
        <= payload_max_i
    )
    mom_min = [(round(p.peso_kg * (pos.brazo_m - avion.cg_min_m) * 1000), x[(p.id, pos.id)]) for p in paquetes for pos in avion.posiciones]
    mom_max = [(round(p.peso_kg * (pos.brazo_m - avion.cg_max_m) * 1000), x[(p.id, pos.id)]) for p in paquetes for pos in avion.posiciones]
    modelo.Add(sum(c * v for c, v in mom_min) >= 0)
    modelo.Add(sum(c * v for c, v in mom_max) <= 0)
    modelo.Maximize(
        sum(round(p.ingreso_usd * ESCALA_PLATA_PESO) * x[(p.id, pos.id)] for p in paquetes for pos in avion.posiciones)
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = tiempo_s
    solver.parameters.num_search_workers = threads
    t0 = time.time()
    status = solver.Solve(modelo)
    t = time.time() - t0
    nombres = {cp_model.OPTIMAL: "Optimal", cp_model.FEASIBLE: "Feasible",
               cp_model.INFEASIBLE: "Infeasible", cp_model.UNKNOWN: "Not Solved"}
    estado = nombres.get(status, str(status))
    objetivo = solver.ObjectiveValue() / ESCALA_PLATA_PESO if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None
    return estado, objetivo, t


SOLVERS = {
    "CBC (PuLP, actual)": lambda pq, av, fs, t, h: _resolver_pulp(pq, av, fs, t, h),
    "CBC (OR-Tools)": lambda pq, av, fs, t, h: _resolver_pywraplp("CBC", pq, av, fs, t, h),
    "SCIP (OR-Tools)": lambda pq, av, fs, t, h: _resolver_pywraplp("SCIP", pq, av, fs, t, h),
    "HiGHS (OR-Tools)": lambda pq, av, fs, t, h: _resolver_pywraplp("HIGHS", pq, av, fs, t, h),
    "CP-SAT (OR-Tools)": lambda pq, av, fs, t, h: _resolver_cp_sat(pq, av, fs, t, h),
}


def _imprimir_fila(nombre: str, n: int, estado: str, objetivo, t: float, threads: int):
    obj_str = f"{objetivo:,.0f}" if objetivo is not None else "—"
    print(f"{nombre:<20} n={n:<6} hilos={threads:<2} {estado:<12} obj={obj_str:>10}  t={t:6.1f}s", flush=True)


def barrido_tamanos(tamanos: list[int]) -> None:
    avion = crear_avion("B767F")
    print(f"=== Barrido por tamaño de catálogo (B767F, timeLimit={TIEMPO_LIMITE_S}s, 1 hilo) ===\n")
    for n in tamanos:
        paquetes = _catalogo(n)
        for nombre, fn in SOLVERS.items():
            estado, objetivo, t = fn(paquetes, avion, 0.85, TIEMPO_LIMITE_S, 1)
            _imprimir_fila(nombre, n, estado, objetivo, t, 1)
        print()


def barrido_hilos(n: int = 8000) -> None:
    avion = crear_avion("B767F")
    paquetes = _catalogo(n)
    print(f"=== Comparativa de hilos (B767F, n={n}, timeLimit={TIEMPO_LIMITE_S}s) ===\n")
    for nombre, fn in SOLVERS.items():
        if nombre.startswith("CBC (PuLP"):
            continue  # threads=1 vía PuLP ya se ve en el barrido de tamaños
        for threads in (1, 2, 4):
            estado, objetivo, t = fn(paquetes, avion, 0.85, TIEMPO_LIMITE_S, threads)
            _imprimir_fila(nombre, n, estado, objetivo, t, threads)
        print()


def verificar_coherencia(n: int = 400) -> None:
    """Corre todos los solvers en un catálogo chico (donde todos deberían
    llegar al óptimo real) y compara el ingreso obtenido — si no coinciden,
    alguna de las formulaciones (probablemente el escalado de CP-SAT) tiene
    un bug, y no hay que confiar en el resto de la comparativa."""
    avion = crear_avion("B767F")
    paquetes = _catalogo(n)
    print(f"=== Chequeo de coherencia entre formulaciones (n={n}, debería dar el mismo ingreso óptimo) ===\n")
    objetivos = {}
    for nombre, fn in SOLVERS.items():
        estado, objetivo, t = fn(paquetes, avion, 0.85, 30, 1)
        objetivos[nombre] = objetivo
        _imprimir_fila(nombre, n, estado, objetivo, t, 1)
    valores = [round(v) for v in objetivos.values() if v is not None]
    if len(set(valores)) > 1:
        print(f"\n⚠️  Los solvers NO coinciden en el óptimo: {objetivos} — revisar las formulaciones antes de confiar en el resto.")
    else:
        print(f"\n✅ Todos los solvers coinciden en ${valores[0]:,.0f} — las formulaciones son equivalentes.")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rapido", action="store_true", help="Tamaños chicos, para iterar rápido")
    parser.add_argument("--hilos", action="store_true", help="Solo la comparativa de threads a n=8000")
    parser.add_argument("--sin-coherencia", action="store_true", help="Salta el chequeo de coherencia inicial")
    args = parser.parse_args()

    if not args.sin_coherencia:
        verificar_coherencia()

    if args.hilos:
        barrido_hilos()
    else:
        barrido_tamanos(TAMANOS_RAPIDOS if args.rapido else TAMANOS_DEFECTO)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
