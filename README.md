# Optimizador de espacio de carga aérea (POC)

Prueba de concepto de **planificación de carga aérea (air cargo load
planning)**: dado un catálogo de paquetes disponibles para un vuelo (en este
caso, cajas de exportación de flores en temporada alta) y uno o más aviones
con posiciones de pallet limitadas, decide **qué paquetes transportar y cómo
acomodarlos en 3D dentro de cada pallet** para maximizar el ingreso total,
respetando peso, volumen y balance (centro de gravedad) del avión.

Ver la formulación matemática completa (variables, función objetivo,
restricciones) en [`docs/formulacion_matematica.md`](docs/formulacion_matematica.md)
— también disponible dentro de la app, en el panel desplegable
"Ver formulación matemática del modelo".

## Enfoque

El problema combina selección de carga (knapsack multidimensional),
bin-packing 3D y balance de peso — resolverlo todo junto de forma exacta es
NP-duro. Por eso se separa en dos etapas:

1. **Etapa A — Selección y asignación (MILP, `PuLP`/CBC):** decide qué
   paquetes viajan y en qué posición de pallet, usando peso y volumen como
   recursos agregados, con margen de seguridad volumétrico y restricción de
   centro de gravedad linealizada.
2. **Etapa B — Empaquetado geométrico 3D (`py3dbp`):** para los paquetes que
   la Etapa A asignó a cada pallet, calcula la posición `(x, y, z)` real de
   cada caja. Si algo no entra geométricamente, descarta primero las cajas de
   menor ingreso por m³ y reintenta.

## Estructura del proyecto

```
app.py                              # App Streamlit (UI + orquestación)
src/optimizador/
  entidades.py                      # Paquete, PosicionCarga, Avion
  datos_simulados.py                # Generador de carga simulada (temporada de flores)
  optimizador_carga.py              # Etapa A: MILP de selección + asignación
  empaquetado_3d.py                 # Etapa B: bin-packing 3D real por pallet
  visualizacion.py                  # Figuras 3D (Plotly) del avión y de cada pallet
docs/formulacion_matematica.md      # Modelo matemático detallado
```

## Cómo correr el POC

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Luego, en la app:

1. Elige el modelo de avión (`B767F` widebody de 10 pallets, o `B737F`
   narrowbody de 5 pallets) y cuántas cajas de flores simular.
2. Click en **"Optimizar carga del avión"**.
3. Explora la vista 3D del avión completo, el detalle por pallet y los
   paquetes que no lograron embarcarse (y por qué).

Con catálogos grandes (>1000 cajas) el avión empieza a saturarse en volumen
—no en peso, que es lo típico en carga de flores— y el optimizador debe
elegir qué dejar en tierra para maximizar ingreso. Con catálogos chicos todo
cabe y el ingreso capturado es 100%.

## Datos simulados

Los paquetes se simulan como cajas estándar de exportación de flores (QB /
HB / FB), con variedades (rosas, claveles, hortensias, alstroemerias,
crisantemos, gypsophila), clientes, destinos y una tarifa USD/kg por
variedad que aproxima precios de temporada alta (p.ej. San Valentín). Los
aviones se modelan con posiciones de pallet estándar (PMC ~223×317 cm),
capacidad de peso por posición, y un brazo de momento para el cálculo de
balance.

## Próximos pasos posibles

- Multi-avión / multi-ruta: repartir un mismo catálogo de carga entre varios
  vuelos disponibles.
- Restricciones de compatibilidad (carga refrigerada, no apilable, hazmat).
- Reoptimización cuando llega carga de último minuto (*late tender*).
- Reemplazar la heurística de empaquetado 3D por un solver exacto
  (CP-SAT / OR-Tools) para catálogos más chicos donde valga la pena.

Ver la sección 5 de [`docs/formulacion_matematica.md`](docs/formulacion_matematica.md)
para más detalle.
