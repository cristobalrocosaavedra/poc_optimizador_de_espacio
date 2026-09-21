---
name: probar-optimizador
description: Stress-test del motor de optimización (MILP + empaquetado 3D) contra varios escenarios comunes y de borde a la vez, sin depender de la UI. Úsalo antes de dar por bueno cualquier cambio a optimizador_carga.py, empaquetado_3d.py, entidades.py, datos_simulados.py, o al panel de diagnóstico de app.py.
---

# Probar el optimizador contra varios escenarios

## Cuándo usar esto

Este proyecto no tiene tests automatizados formales (pytest, etc.) — todo se
valida corriendo el motor real. El riesgo de probar "un solo caso feliz" ya
se materializó varias veces en este repo: comportamientos que solo aparecen
con catálogos grandes (tiempos de MILP), con la meta pegada al techo
(el solver se cuelga si el margen es muy chico), o con % altos de carga no
apilable (violaciones de apilamiento que antes llegaban a 108 en un catálogo
grande y no se veían en catálogos chicos).

Usa este skill:
- Antes de mergear cualquier cambio a `src/optimizador/*.py`.
- Antes de dar por cerrado un cambio al panel de diagnóstico de `app.py`
  (necesitas ver los mismos números en varios escenarios, no solo el que
  motivó el cambio).
- Cuando el usuario reporta "no estamos optimizando suficiente" o algo
  similar — antes de teorizar, corre esto para ver si es un patrón general o
  específico de su configuración.

## Cómo usarlo

```bash
cd /home/user/poc_optimizador_de_espacio
source .venv/bin/activate
python3 scripts/probar_escenarios.py
```

Corre ~12 escenarios (ver la lista `ESCENARIOS` en el script) que cubren:
meta fácil de cumplir, meta inalcanzable por mucho, meta pegada al techo
(dispara el shortcut de la Fase 2), modo "maximizar ingreso" sin meta,
ambos modelos de avión (B767F/B737F), stock diminuto frente a una meta
grande, extremos de % no apilable/riesgo alto (incluido un control en 0%
para comparar), meta = 0, 100% de carga obligatoria, factor de seguridad de
volumen bajo, y el catálogo más grande soportado (8000, tope del slider).

Para correr uno solo mientras iteras en un fix:
```bash
python3 scripts/probar_escenarios.py --solo "meta pegada al techo"
```

Cada escenario valida automáticamente (no solo reporta números, también
falla si algo está mal):
- **Capacidad**: nada excede peso/volumen por posición, payload del avión,
  ni el rango de CG (si esto falla, hay un bug real en la Etapa A o en cómo
  se extrae el resultado del MILP).
- **Apilamiento**: una auditoría *independiente* de la geometría 3D
  colocada — deliberadamente no reutiliza la función interna de
  `empaquetado_3d.py`, para que un bug ahí no quede invisible a la prueba.
- **Estado del solver**: que no sea algo distinto de `Optimal` o
  `Not Solved` (este último es el fallback esperado cuando la Fase 2 no
  converge a tiempo — ver `optimizar_con_meta()`).

El script termina con exit code 1 si algo falló, así que también sirve como
gate simple aunque no haya CI configurado en el repo todavía.

## Qué NO cubre

Esto prueba el motor (`src/optimizador/`), no la UI de Streamlit en sí
(sliders, data_editor, el flujo de "confirmar despacho", los mensajes
exactos que arma `app.py`). Para eso, sigue usando Playwright contra la app
corriendo (`streamlit run app.py --server.headless true --server.port
8501`) como se hizo para validar el panel de diagnóstico — en este entorno
Playwright está disponible vía Node (`/opt/node22/lib/node_modules/playwright`,
navegador en `/opt/pw-browsers/chromium`), no vía pip. Ver `CLAUDE.md` para
el patrón exacto (`.cjs` + `require()` con ruta absoluta).

## Si agregas un escenario nuevo

Agrégalo a la lista `ESCENARIOS` en `scripts/probar_escenarios.py` con un
`nombre` que describa qué caso de borde cubre (no "test 13"). Si el bug que
estás arreglando vino de un reporte del usuario con parámetros específicos
(semilla, tamaño de stock, meta), agrégalos tal cual como escenario nuevo en
vez de solo arreglar el código — así ese caso queda cubierto para siempre.
