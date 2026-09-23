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
   recursos agregados (el volumen ya descuenta el contorno real del pallet,
   ver más abajo), con margen de seguridad volumétrico y restricción de
   centro de gravedad linealizada.
2. **Etapa B — Empaquetado geométrico 3D (`py3dbp`):** para los paquetes que
   la Etapa A asignó a cada pallet, calcula la posición `(x, y, z)` real de
   cada caja, respetando que nada quede apoyado sobre carga no apilable o de
   alto riesgo. Si algo no entra (geometría o apilamiento), queda reportado
   como no colocado.

### Contorno del pallet y carga no apilable

Un pallet junto al fuselaje no es una caja recta: la altura de estiba se
recorta hacia la pared curva del avión. Cada posición se modela con
**bandas** transversales (borde bajo junto al fuselaje, banda alta hacia el
pasillo/quilla central) en vez de un volumen rectangular parejo — esto
afecta tanto el volumen disponible en la Etapa A como la forma real del
empaquetado en la Etapa B. Los aviones anchos (`B767F`) se arman como pares
de pallets **izquierdo/derecho** por estación a lo largo del fuselaje (el
layout real de la mayoría de los aviones de carga), cada uno con el
contorno recortado hacia su lado exterior.

Cada paquete además puede marcarse **no apilable** (nada puede apoyarse
encima) o de **alto riesgo** (frágil/sensible: siempre no apilable, y se
marca aparte en el 3D). El empaquetado 3D respeta esto de verdad — no es
solo una etiqueta informativa.

### Modo "cumplir monto objetivo" y stock compartido en fila

El ingreso no siempre es algo que el modelo deba maximizar: en la operación
real, el área comercial ya decide cuánto debe facturar cada avión, y la
tarea es seleccionar carga que se acerque a ese monto **sin pasarse por
mucho** (el monto es piso y techo, no un mínimo libre — no tiene sentido
cargar bastante más de lo pedido solo por llenar espacio) y, entre esas
opciones, aprovechar el espacio lo mejor posible (ver sección 2.1 de la
formulación).

Además, ese monto es *por avión*, y los aviones despachan **en fila desde un
mismo stock compartido** (el stock de temporada, no un catálogo por vuelo):
el avión #1 toma lo que necesita para cumplir su meta, lo que sobra queda
disponible para el avión #2, y así sucesivamente. La app modela esto
directamente — ver "Cómo correr el POC" más abajo.

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

1. Genera el **stock de temporada** (el tamaño simula todo lo disponible
   para la fila completa de aviones, no solo el primero). Revisa o edita el
   stock en la tabla (agrega filas, corrige valores, marca carga no
   apilable o de alto riesgo).
2. Para el avión actual (#1, #2, ...): elige su modelo, y su monto objetivo
   si vas a usar el modo "Cumplir un monto objetivo" (el modo por defecto). El
   modelo **no optimiza ese número en dólares** — ya viene decidido por el
   área comercial y se ingresa tal cual. La app sugiere un monto de partida
   cercano al techo real de capacidad de ese avión con su disponibilidad
   actual (en la práctica, casi siempre se pide facturar cerca del máximo
   del espacio disponible) — es editable, ajústalo si el área comercial te
   dio otro número. Lo que el modelo sí optimiza es **qué paquetes elegir**
   del stock para acercarse a ese monto sin pasarse por mucho (no es un piso
   libre — no carga de más solo por llenar espacio), y entre las
   combinaciones que logran eso, cómo usar el volumen/peso disponible del
   avión de la forma más eficiente posible.
3. Click en **"Optimizar carga del avión"** y revisa el resultado: vista 3D
   completa (fuselaje, contorno de pallet, layout izquierdo/derecho),
   detalle por pallet, y qué quedó fuera. Si no se alcanza la meta, el panel
   **"¿Por qué este resultado? (diagnóstico)"** explica si el cuello de
   botella fue volumen o peso (agregado o de algún pallet en particular),
   si el ingreso mostrado ya es el máximo matemáticamente posible con ese
   stock y ese avión, y qué paquetes de mayor valor quedaron fuera.
4. Click en **"Confirmar despacho y pasar al siguiente"**: descuenta del
   stock lo que ese avión realmente cargó, guarda el resultado en el
   historial de despacho, y pasa al avión siguiente — que toma lo que
   sobró.
5. Repite para cada avión de la fila. Si el stock restante supera lo que la
   fila que queda puede absorber, la app avisa antes de que sigas cargando.
   Al despachar el último avión planeado, la fila se cierra (no puedes
   seguir agregando aviones "de más" sin subir explícitamente "Aviones
   planeados en esta fila" en el sidebar). El botón "Generar stock nuevo"
   reinicia todo (stock + historial) si quieres empezar de cero.

Con catálogos grandes (>1000 cajas) el avión empieza a saturarse en volumen
—no en peso, que es lo típico en carga de flores— y el optimizador debe
elegir qué dejar en tierra para maximizar ingreso. Con catálogos chicos todo
cabe y el ingreso capturado es 100%.

## Datos simulados

Los paquetes se simulan como cajas estándar de exportación de flores (QB /
HB / FB), con variedades (rosas, claveles, hortensias, alstroemerias,
crisantemos, gypsophila), clientes, destinos y una tarifa USD/kg por
variedad que aproxima precios de temporada alta (p.ej. San Valentín). Los
aviones se modelan con posiciones de pallet por estación, capacidad de peso
por posición, y un brazo de momento para el cálculo de balance — **4
modelos de carguero dedicado** disponibles hoy (`B737F`, `B767F`, `MD11F`,
`B777F`, de menor a mayor capacidad), todos con números aproximados
(inspirados en especificaciones públicas típicas, no datos operativos
reales) al mismo nivel de aproximación. Cada avión de la fila también puede
tener una **disponibilidad de capacidad reducida** (manual o sorteada
dentro de un rango): ningún avión vuela realmente con el 100% de su
capacidad estructural libre para carga (derates de combustible/peso), y es
la misma mecánica que más adelante modelará el caso de un avión de
pasajeros con carga compartida (ver hoja de ruta abajo).

## Hoja de ruta

**Ya hecho** (este POC, en este orden): selección + empaquetado 3D con
contorno real de fuselaje y apilamiento correcto; monto objetivo como piso
y techo (no maximiza ingreso libremente); stock compartido en fila entre
aviones con reparto proporcional de carga obligatoria; panel de diagnóstico
de por qué no se alcanza una meta; 4 tipos de avión carguero;
disponibilidad de capacidad variable por vuelo.

**Siguiente etapa — más variabilidad y tipos de operación**:
- **Aviones de pasajeros con carga compartida (belly cargo)**: mucha carga
  de flores real viaja en el compartimento inferior de vuelos de pasajeros,
  no solo en cargueros dedicados. A diferencia de un carguero, ahí la
  capacidad para carga es un **resto variable** (depende de cuánto equipaje
  lleve ESE vuelo en particular, no es un número fijo conocido de
  antemano) y usa contenedores tipo LD3/LD6 en vez de pallets — geometría y
  lógica de capacidad distintas a lo que se modela hoy. La disponibilidad
  variable ya implementada es la base para esto (mismo mecanismo, con un
  rango más agresivo y quizás ligado a un "% de ocupación de pasajeros"
  simulado en vez de un sorteo genérico).
- Restricciones de compatibilidad adicionales (carga refrigerada, hazmat,
  incompatibilidad entre productos).

**Etapas futuras**:
- Optimizar la fila completa de aviones a la vez (qué avión debería llevarse
  qué, viendo toda la fila de una), en vez de secuencial y greedy como hoy
  — mejores resultados globales, pero un problema bastante más grande.
- Balance lateral (izquierdo/derecho), no solo longitudinal — hoy el CG solo
  considera el eje del fuselaje.
- Reoptimización cuando llega carga de último minuto (*late tender*).
- Reemplazar la heurística de empaquetado 3D por un solver exacto
  (CP-SAT / OR-Tools) para catálogos más chicos donde valga la pena — ver
  `.claude/skills/comparar-solvers/` para la comparativa ya hecha de
  solvers para la Etapa A (selección), que es un punto de partida para esto.
- Reemplazar los datos simulados (tarifas, densidades, specs de avión) por
  datos reales de operación.

Ver la sección 5 de [`docs/formulacion_matematica.md`](docs/formulacion_matematica.md)
para más detalle técnico de lo ya implementado.
