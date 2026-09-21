---
name: comparar-solvers
description: Comparativa empírica de solvers MILP (CBC, SCIP, HiGHS, CP-SAT vía OR-Tools) contra el mismo problema de la Etapa A, a distintos tamaños de catálogo y con distinto número de hilos. Úsalo antes de decidir si conviene cambiar el solver de producción (CBC/PuLP), o antes de subir el tope de tamaño de stock que soporta la app.
---

# Comparar solvers MILP para la Etapa A

## Cuándo usar esto

CBC (el solver actual, vía PuLP) puede no encontrar ninguna solución entera
factible en catálogos grandes (~8000 paquetes = 96.000 variables binarias
para el B767F) dentro del tiempo límite — ver el gotcha correspondiente en
`CLAUDE.md`. Antes de decidir cambiar de solver (una dependencia nueva,
~30MB, y una superficie de código distinta), usa este skill para tener
números reales en vez de intuición.

Úsalo:
- Antes de cambiar `pulp.PULP_CBC_CMD` por otra cosa en
  `optimizador_carga.py`.
- Antes de subir el tope del slider "Tamaño del stock a simular" en
  `app.py` (hoy 8000).
- Si el usuario pregunta "¿esto escala?" o "¿qué pasa con catálogos más
  grandes?" — corre esto en vez de especular.

## Cómo usarlo

```bash
cd /home/user/poc_optimizador_de_espacio
source .venv/bin/activate
pip install ortools   # si no está — ver notas de licencia abajo

python3 scripts/comparar_solvers.py               # barrido completo (~10-15 min)
python3 scripts/comparar_solvers.py --rapido       # tamaños chicos, para iterar
python3 scripts/comparar_solvers.py --hilos        # ¿paraleliza? comparativa 1/2/4 hilos a n=8000
python3 scripts/comparar_solvers.py --sin-coherencia  # salta el chequeo de coherencia (más rápido si ya confías en las formulaciones)
```

El barrido completo (5 tamaños × 5 solvers × hasta 30s cada uno) puede
tomar 10-15 minutos — **corrélo con `run_in_background: true`** si estás
usando el Bash tool de Claude Code, no bloquees la sesión esperándolo.

Antes de confiar en los resultados grandes, el script corre un chequeo de
coherencia en un catálogo chico (n=400): todos los solvers deberían llegar
exactamente al mismo ingreso óptimo. Si no coinciden, alguna formulación
tiene un bug (típicamente el escalado a enteros de CP-SAT) — no uses los
números del barrido grande hasta que esto pase.

## Solvers que cubre (y los que no)

Gratuitos, probados en este entorno vía OR-Tools (`pip install ortools`):
**CBC** (el mismo algoritmo que usa PuLP, para separar "es CBC" de "es el
wrapper de PuLP"), **SCIP**, **HiGHS** (viene bundleado en OR-Tools acá, no
hace falta `pip install highspy` aparte), **CP-SAT** (necesita coeficientes
enteros — el script escala peso/volumen/ingreso automáticamente).

**Gurobi y CPLEX (pagos) NO se pueden probar en este entorno** — no hay
librería nativa ni licencia instalada (`pywraplp.Solver.CreateSolver
("GUROBI")` devuelve `None` acá). Si en algún momento se consigue una
licencia de evaluación, este mismo script sirve de base — falta agregar
`_resolver_pywraplp("GUROBI", ...)` a la lista de `SOLVERS` (la función ya
existe genérica, solo hay que registrar el backend). Sin eso, no
recomiendes Gurobi con datos que no se probaron — solo con lo que se sabe
de su reputación general (suele ganarle a los solvers gratis en MIPs
grandes y difíciles, pero acá el problema es una asignación/knapsack con
estructura razonable, no necesariamente el caso donde esa diferencia
importa).

## Qué mirar en los resultados

- **Estado** (`Optimal`/`Feasible`/`Not Solved`): lo primero. Un solver que
  no confirma ni una solución factible en el tiempo límite es peor que uno
  que encuentra una buena solución sin probar optimalidad — no compares
  solo por velocidad si el estado es distinto.
- **`obj` (ingreso)**: a igual estado, más alto es mejor. Si dos solvers
  dicen `Not Solved` compara el objetivo igual (es la mejor solución
  encontrada hasta el corte).
- **Comparativa de hilos**: si el objetivo/estado no mejora de 1→4 hilos,
  ese solver no está aprovechando los cores disponibles en este problema
  particular — no asumas que "más hilos" siempre ayuda, hay que verlo.

## Si decides cambiar el solver de producción

No lo hagas solo con esto — repórtale los números al usuario y que decida
(ya se hizo una vez: encontrar que CBC fallaba en n=8000 fue insight de un
stress test, pero cambiar el solver de producción es una decisión de
arquitectura, no algo para hacer unilateralmente). Si el usuario aprueba el
cambio: agrega `ortools` a `requirements.txt`, reemplaza la construcción
del problema en `optimizador_carga.py` (hay una versión de referencia ya
escrita en `_resolver_pywraplp`/`_resolver_cp_sat` de este script, pero
production necesita conservar la extracción de resultado, `ResultadoOptimizacion`,
etc.), y vuelve a correr **`.claude/skills/probar-optimizador/`** completo
para confirmar que nada se rompió con el solver nuevo.
