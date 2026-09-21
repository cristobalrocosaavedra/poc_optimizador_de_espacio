# Contexto técnico del proyecto (para Claude / futuras sesiones)

Este archivo es memoria de trabajo: lo que ya se probó, se rompió, se midió o
se decidió — para no volver a gastar tokens redescubriéndolo. El
`README.md` es para el usuario final; esto es para quien toque el código.

## Qué es esto

POC de planificación de carga aérea (flores, temporada alta). Dos etapas:
**Etapa A** = MILP de selección/asignación (`PuLP`/CBC, en
`src/optimizador/optimizador_carga.py`). **Etapa B** = empaquetado 3D real
por pallet (`py3dbp` con lógica de colocación custom, en
`src/optimizador/empaquetado_3d.py`). La UI es `app.py` (Streamlit). La
formulación matemática completa vive en `docs/formulacion_matematica.md` —
léelo antes de tocar restricciones del MILP, ya tiene el razonamiento de
cada decisión.

## Cómo correr y probar

```bash
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
streamlit run app.py --server.headless true --server.port 8501
```

Para probar la UI de punta a punta (no hay tests automatizados formales,
todo se valida corriendo la app real) usa el skill
**`.claude/skills/probar-optimizador/`** — automatiza Playwright contra
varios escenarios a la vez y extrae los KPIs/diagnóstico de cada uno. Úsalo
antes de dar por buena cualquier cambio al MILP, al empaquetado 3D o al
panel de diagnóstico: un solo caso feliz no alcanza, este problema tiene
comportamiento distinto según si la meta es alcanzable, está pegada al
techo, o el stock es chico vs. grande.

En este entorno, Playwright está disponible por Node, no por pip:
```bash
node -e "require('/opt/node22/lib/node_modules/playwright')"   # confirma que existe
```
usa `chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })` y
`require()` con la ruta absoluta al módulo (import ESM con `NODE_PATH` no
resuelve bien en este runtime — usa `.cjs` + `require`).

## Gotchas ya resueltos (no los vuelvas a pisar)

- **`Decimal` vs `float` en py3dbp**: `Bin.get_total_weight()` siempre
  devuelve `Decimal`, incluso sin llamar `Packer.pack()`. Si sumas contra un
  `float` revienta con `TypeError`. Hay que llamar `bin_.format_numbers(1)`
  y `item.format_numbers(1)` después de crear cada `Bin`/`Item` — ya se hace
  en `_bins_por_banda()` y `empaquetar_posicion()`.
- **Apilable/riesgo alto no es solo una etiqueta**: `_genera_apoyo_invalido()`
  en `empaquetado_3d.py` tiene que chequear **las dos direcciones** — item
  nuevo apoyándose sobre algo no apilable, Y algo no apilable llegando
  después a apoyarse debajo de algo que ya estaba puesto — porque los items
  se colocan por densidad de valor, no por altura. Si tocas esa función,
  vuelve a correr un catálogo grande y cuenta violaciones (ver commit
  history: bajó de 108 → 7 → 0 con ambas direcciones).
- **Orden de empaquetado importa mucho**: apilables primero, no apilables al
  final (`sorted(paquetes, key=lambda p: (not p.permite_apilado_encima,
  -p.densidad_valor))`). Con el orden ingenuo (solo por valor) el volumen
  realmente aprovechado cae de ~74% a ~64% en catálogos grandes. No
  "simplifiques" ese sort sin volver a medir.
- **Techo práctico de empaquetado 3D**: aun con 0% de carga no apilable, la
  heurística de py3dbp + el contorno de fuselaje no llega a 100% de volumen
  — el techo real observado es **~74-75%**. Si una prueba nueva da mucho
  menos que eso, hay una regresión; si da mucho más con carga no apilable
  >0%, sospecha del check de apilamiento, no lo celebres sin revisar.
- **CBC puede "resolver" sin encontrar ninguna solución factible**: con
  catálogos grandes (n=8000 en B767F = 96.000 variables binarias) CBC puede
  agotar el tiempo límite sin confirmar ni una sola solución entera
  factible ("No feasible solution found" en su log, estado PuLP
  `"Not Solved"`). Cuando eso pasa, `var.value()` de PuLP igual devuelve lo
  último que tocó la relajación LP — **valores fraccionarios** (p.ej. 0.48,
  0.52) — y redondear eso a ">0.5" puede violar la capacidad real de un
  pallet (se detectó con `scripts/probar_escenarios.py`, escenario
  "catálogo grande": 9 de 12 posiciones excedían su volumen). Por eso
  `_extraer_resultado()` en `optimizador_carga.py` siempre repara la
  asignación con `_reparar_capacidad_posicion()` después de extraerla — sin
  importar el estado del solver. Si tocas la extracción del resultado, no
  quites ese reparo pensando que "el MILP ya lo garantiza": no lo garantiza
  cuando el solver no terminó.
- **El tope de peso por posición manda antes que el payload del avión**: en
  el B767F cada una de las 12 posiciones tiene `peso_max_kg=2600` — sumado,
  eso es 31.200 kg, bastante menos que el `peso_max_carga_kg=52.000` del
  avión. Con carga densa (probado forzando 65-95 kg/caja en
  `scripts/probar_escenarios.py`, escenario "carga muy densa"), el peso se
  topa en ~31.200 kg, no en 52.000 — el `peso_max_carga_kg` del avión casi
  nunca se alcanza en la práctica porque los topes individuales de pallet
  llegan primero. Mismo patrón que el de volumen (ver el punto de "el MILP
  asigna por posición" más abajo), aplicado a peso. No es un bug, pero si
  agregas diagnóstico de peso en `app.py` como el que ya existe para
  volumen, recuerda esto.
- **"Obligatorio" puede exceder la capacidad de un avión — es alcanzable
  desde la UI, no solo en pruebas extremas**: `pct_obligatorio` se aplica
  sobre **todo el stock generado** (pensado para la fila completa de
  aviones), pero `app.py` le pasa el stock remanente **completo** a la
  optimización de un solo avión — no acota cuántas obligatorias le tocan a
  ESE avión en particular. Con `n=8000` (el tope del slider) y
  `pct_obligatorio=20%` (dentro del rango 0-40% que el slider permite), la
  carga obligatoria SOLA ya pide ~93.7 m³ y solo hay ~55.8 m³ permitidos —
  el MILP da `"Infeasible"` de verdad (no timeout). El reparo de capacidad
  (`_reparar_capacidad_posicion`) lo rescata igual — nunca revienta ni viola
  capacidad — pero termina descartando en silencio la mayoría de las cajas
  obligatorias que no caben (de ~1580 obligatorias, solo ~650 embarcan).
  **Esto no está comunicado en la UI todavía** — si el usuario pide agregar
  esa visibilidad (un aviso tipo "se descartaron N cajas obligatorias por
  falta de capacidad"), es un cambio chico en el panel de diagnóstico de
  `app.py`, pero no lo agregues sin que lo pidan. Escenario de referencia:
  `scripts/probar_escenarios.py`, "obligatorio excede la capacidad".
- **Comparativa de solvers ya hecha**: antes de asumir que hay que cambiar
  de CBC, usa `.claude/skills/comparar-solvers/` — ya se armó y validó (las
  5 formulaciones coinciden en el óptimo de un catálogo chico) un
  comparador de CBC/SCIP/HiGHS/CP-SAT vía OR-Tools a distintos tamaños. No
  reinventes ese harness ni vuelvas a redescubrir empíricamente qué solver
  aguanta qué tamaño — corre el script.
- **Plotly y freeze del navegador**: nunca emitas un trace por caja/arista.
  Con ~850 cajas eso son ~1000 traces y el navegador se cuelga (WebGL). Todo
  en `visualizacion.py` usa **batching** (`_cubos_batch`, `_contornos_batch`,
  etc.) — combina geometría de N cajas en un solo `Mesh3d`/`Scatter3d`. Si
  agregas una figura nueva, sigue el mismo patrón, no emitas un trace por
  elemento.
- **La Fase 2 de `optimizar_con_meta()` puede colgarse cerca del óptimo**:
  pedirle al solver "ingreso ≥ prácticamente el máximo" es un problema de
  factibilidad muy duro (casi la única combinación lo logra). Por eso existen
  `TOLERANCIA_META_RELATIVA` (0.02, salta la Fase 2 si la meta ya está ahí) y
  `MARGEN_META_INALCANZABLE` (0.10, le da margen real a la Fase 2 cuando la
  meta es inalcanzable). Si tocas esos números, vuelve a medir con un
  catálogo de ~3000-8000 (ver tiempos abajo) — no confíes en la intuición
  acá, ya se probó que un margen chico cuelga el solver.
- **El MILP asigna por posición de pallet, no contra un pozo agregado**: cada
  una de las 12 posiciones (B767F) tiene su propio tope de peso/volumen. Un
  avión puede verse con margen "en total" y aun así tener varios pallets
  individualmente llenos — ese es el cuello de botella real, y el panel de
  diagnóstico en `app.py` ya lo chequea por posición, no solo agregado (ver
  `posiciones_llenas_vol`/`posiciones_llenas_peso`). Si agregas más
  diagnóstico, sigue este mismo nivel de granularidad, el agregado solo
  engaña.
- **`UnicodeDecodeError` en Windows**: cualquier `Path.read_text()` sin
  `encoding="utf-8"` explícito usa cp1252 en Windows y revienta con el
  markdown de `docs/`. Todos los `read_text()` en `app.py` ya lo tienen —
  no lo quites.

## Tiempos de solve medidos (referencia, no los repitas de cero)

`optimizar_con_meta()` (dos solves MILP) con B767F, `factor_seguridad=0.85`:

| n paquetes | tiempo aprox. |
|---|---|
| 3000  | ~12 s |
| 5000  | ~27 s |
| 8000  | ~34 s |

Por eso el slider de stock en `app.py` tiene tope 8000 con aviso — no lo
subas sin volver a medir, CBC con `gapRel=0.02` y `timeLimit` puede degradar
mal en catálogos más grandes.

## Decisiones de producto ya tomadas (no las reabras sin que el usuario lo pida)

- El monto objetivo por avión **no lo optimiza el modelo** — es un dato
  externo (área comercial). El modelo optimiza selección + espacio para
  alcanzarlo. No conviertas esto en "maximizar ingreso" por defecto.
- El stock es **compartido y se agota en fila** entre aviones (no un
  catálogo por vuelo). Ver `historial_despachos`/`num_avion` en
  `session_state`.
- La fila de aviones es **secuencial y greedy** a propósito — optimizar la
  fila completa de una es un problema más grande que el usuario explícitamente
  no ha pedido resolver todavía. No lo propongas de nuevo sin que lo pida:
  ya se hizo una vez y el feedback fue "no te adelantes a los hechos".
- Balance lateral, hazmat, reoptimización por late-tender, solver exacto de
  bin-packing: todo en "Próximos pasos posibles" del README, ninguno pedido
  aún. Mismo criterio: no los implementes de forma proactiva.

## Convención de ramas/PRs de este repo

Un único feature branch (`claude/...`) que se mergea seguido — cada PR
mergeado significa que el branch para el siguiente cambio debe reiniciarse
desde `origin/main` (no seguir apilando sobre historia ya mergeada). Este es
el patrón operativo de las sesiones de Claude Code en este repo, no una regla
de negocio del optimizador.
