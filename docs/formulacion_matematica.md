# Formulación matemática — Optimizador de carga aérea

## 1. Contexto del problema

Una aerolínea de carga opera **aviones** en una ruta determinada. Cada avión
tiene un número fijo de **posiciones de carga** (pallets / ULD) con límites
de peso y volumen propios, y una posición física dentro del avión (relevante
para el balance / centro de gravedad).

Antes del vuelo se conoce un **catálogo de paquetes disponibles** para
transportar (en el caso de prueba: cajas de flores de exportación en
temporada alta). Cada paquete tiene peso, volumen y un ingreso asociado
(tarifa de flete). No siempre entran todos los paquetes disponibles: el
objetivo es **elegir qué paquetes llevar y en qué posición**, maximizando el
ingreso total, sin violar las restricciones físicas del avión.

Este es un problema combinado de:

1. **Selection / Knapsack multidimensional** — qué paquetes cargar.
2. **Bin packing 3D** — cómo acomodarlos geométricamente dentro de cada
   pallet.
3. **Balance de carga (weight & balance)** — el centro de gravedad resultante
   debe quedar dentro del rango operacional seguro.

Resolver 1+2+3 de forma exacta y simultánea es NP-duro y poco práctico para
una POC. Por eso el enfoque se separa en **dos etapas**:

- **Etapa A (MILP):** selección de paquetes y asignación a *posición de
  pallet*, usando volumen y peso como recursos agregados (relajación del
  bin-packing 3D real) + restricción de balance.
- **Etapa B (empaquetado geométrico):** para los paquetes que la etapa A
  asignó a cada pallet, se corre un algoritmo de bin-packing 3D real
  (`py3dbp`) que calcula la posición `(x, y, z)` de cada caja dentro del
  pallet y verifica que efectivamente entren. Si algo no cabe (poco
  frecuente gracias al factor de seguridad de volumen de la etapa A), se
  descartan las cajas de menor ingreso hasta que la posición sea factible.

---

## 2. Etapa A — Modelo de asignación (MILP)

### Índices

- `i ∈ I`: paquetes disponibles.
- `p ∈ P`: posiciones de carga (pallets) del avión.

### Datos

| Símbolo | Descripción |
|---|---|
| `ingreso_i` | Ingreso (USD) si el paquete `i` se transporta |
| `peso_i` | Peso del paquete `i` (kg) |
| `vol_i` | Volumen del paquete `i` (m³) |
| `obligatorio_i` | 1 si el paquete `i` tiene contrato de embarque garantizado |
| `W_p` | Capacidad máxima de peso de la posición `p` (kg) |
| `V_p` | Capacidad máxima de volumen de la posición `p` (m³) |
| `brazo_p` | Brazo de momento de la posición `p` respecto a la referencia del avión (m) |
| `PesoMax_avión` | Payload máximo de carga del avión (kg) |
| `cg_min`, `cg_max` | Límites del centro de gravedad admisible, expresados como brazo promedio (m) |
| `f_seg` | Factor de seguridad volumétrico (< 1) para dejar margen al empaquetado 3D real |
| `f_disp` | Disponibilidad real de capacidad de este vuelo (≤ 1) — cuánto de la capacidad nominal (peso y volumen) está realmente libre para carga |

### Variables de decisión

```
x_{i,p} ∈ {0, 1}      1 si el paquete i se asigna a la posición p
```

### Función objetivo

```
maximizar   Σ_i Σ_p  ingreso_i · x_{i,p}
```

### Restricciones

**(1) Cada paquete se asigna a lo más a una posición (o no viaja):**
```
Σ_p x_{i,p}  ≤ 1                 ∀ i
```

**(2) Capacidad de peso por posición:**
```
Σ_i peso_i · x_{i,p}  ≤  f_disp · W_p     ∀ p
```

**(3) Capacidad de volumen por posición (con margen de seguridad):**
```
Σ_i vol_i · x_{i,p}  ≤  f_disp · f_seg · V_p     ∀ p
```

`V_p` ya no es un simple `largo × ancho × alto`: cada posición tiene un
**contorno** (ver sección 3) que recorta la altura útil hacia el fuselaje,
así que `V_p` es la suma del volumen de cada banda del contorno. Dos
posiciones con la misma huella en planta pueden tener `V_p` distinto según
si están junto al fuselaje (contorno más agresivo) o hacia el
pasillo/quilla central.

**(4) Payload máximo total del avión:**
```
Σ_p Σ_i peso_i · x_{i,p}  ≤  f_disp · PesoMax_avión
```

`f_disp` (por defecto 1, capacidad nominal completa) representa que un
avión rara vez vuela con el 100% de su capacidad estructural libre para
carga — derates de combustible/peso, y (etapa futura) espacio compartido
con equipaje en un avión de pasajeros. Se aplica por igual a peso y volumen,
y no afecta el rango de CG (`cg_min`/`cg_max`): eso es sobre balance, no
sobre cuánta capacidad total hay disponible.

**(5) Balance / centro de gravedad — linealizado:**

El centro de gravedad real es un cociente (momento total / peso total), lo
que no es lineal. Se linealiza multiplicando ambos lados por el peso total,
quedando dos restricciones lineales equivalentes a "el brazo promedio
ponderado por peso debe estar entre `cg_min` y `cg_max`":

```
Σ_p Σ_i peso_i · x_{i,p} · (brazo_p − cg_min)  ≥ 0
Σ_p Σ_i peso_i · x_{i,p} · (brazo_p − cg_max)  ≤ 0
```

**(6) Paquetes obligatorios (contractuales):**
```
Σ_p x_{i,p} = 1        ∀ i tal que obligatorio_i = 1
```

Este modelo es **lineal entero mixto (MILP)** y se resuelve con `PuLP`
(solver CBC, incluido).

### 2.1 Variante: monto objetivo ya decidido

En operación real, el ingreso a lograr no siempre es algo que el modelo deba
maximizar: muchas veces el área comercial **ya decidió** cuánto debe
facturar el vuelo, y entrega ese monto junto con el catálogo de paquetes
disponibles. En ese caso la función objetivo original deja de tener
sentido — el rol del modelo pasa a ser (a) seleccionar paquetes cuyo
ingreso se acerque a ese monto **sin pasarse por mucho** (el monto objetivo
es piso Y techo, no solo piso — si el área comercial dijo `M`, cargar bastante
más que `M` solo para llenar espacio no es el objetivo) y (b), entre las
combinaciones que logran eso, usar el espacio disponible de la forma más
eficiente posible. Esto se resuelve en dos fases, con el mismo conjunto de
restricciones (1)-(6):

**Fase 1 — el mejor ingreso posible sin exceder el techo.** Se resuelve el
modelo original (maximizar `Σ ingreso_i x_{i,p}`) pero con una restricción
adicional: `Σ ingreso_i x_{i,p} ≤ techo`, donde `techo = M · (1 + δ)` (`δ`
un margen superior chico, del orden del `gapRel` del solver — no dejar que
el ingreso se pase de la meta salvo lo mínimo que la granularidad de las
cajas y la tolerancia del propio solver ya exigen). El resultado,
`ingreso_techo`, es el ingreso más cercano a `M` sin pasarse del margen —
salvo que `M` no sea alcanzable ni con margen, en cuyo caso la restricción
de techo no ata y `ingreso_techo` es directamente el ingreso máximo real del
avión con este stock (mismo valor que se reportaría como `ingreso_max` para
calcular `faltante`, sin necesidad de un solve aparte).

Caso de borde: si hay carga **obligatoria** (restricción (1) con `= 1`) cuyo
ingreso por sí solo ya excede `techo`, la Fase 1 es **infactible de verdad**
(no un timeout) — no existe ninguna asignación que respete el techo y a la
vez embarque toda la carga obligatoria. En ese caso se cae directamente al
modelo original sin restricción de techo (equivalente a la Etapa A de
`optimizar()`): se prioriza no romper el balance del avión ni la semántica
de "obligatorio" antes que respetar el techo en un caso que matemáticamente
no admite ambas cosas a la vez.

**Fase 2 — maximizar espacio sujeto a piso Y techo de ingreso.** Se agrega
`Σ ingreso_i x_{i,p} ≥ piso` (con `piso = ingreso_techo · (1 − ε)`, `ε` la
misma tolerancia chica que arriba — exigir el piso exacto vuelve la sola
factibilidad muy difícil de resolver rápido) **y** se mantiene
`Σ ingreso_i x_{i,p} ≤ techo`, y se cambia la función objetivo a maximizar
el aprovechamiento combinado de volumen y peso:

```
maximizar   (Σ_i Σ_p vol_i · x_{i,p}) / V_avión   +   (Σ_i Σ_p peso_i · x_{i,p}) / PesoMax_avión
sujeto a    piso  ≤  Σ_i Σ_p ingreso_i · x_{i,p}  ≤  techo
            (1)-(6) igual que el modelo original
```

Entre las combinaciones que logran (casi) el mismo ingreso que encontró la
Fase 1, sin pasarse del techo, esta fase elige la que mejor usa el espacio
— nunca la que más ingreso agrega, que es justamente lo que se quería
evitar.

Si `ingreso_techo` ya está prácticamente en `techo` (dentro de esa misma
tolerancia), no queda margen real para reoptimizar por espacio sin
arriesgarse a pasarse del techo, así que se usa directamente el resultado
de la Fase 1.

Si el monto objetivo `M` no es alcanzable ni con margen, se reporta
`faltante = M − ingreso_techo` — pero el piso de la Fase 2 en ese caso NO se
fija pegado a `ingreso_techo` a secas: como la meta ya se perdió de todas
formas y el techo real del avión queda por debajo de `M` (no hay riesgo de
pasarse de lo pedido), insistir en quedar pegado al techo no tiene sentido y
cae en la misma zona dura para el solver que `ε` evita. Se usa un margen
bastante más generoso (`piso = ingreso_techo · (1 − 0.10)`) para que la Fase
2 tenga espacio real donde optimizar — probado sobre un catálogo de
referencia que el ingreso resultante apenas varía entre pedir 100% o 0% del
máximo como piso, así que ese margen cuesta casi nada de plata y gana
bastante espacio utilizado (volumen realmente aprovechado: ~64% exigiendo
el piso pegado al techo, que además el solver no siempre logra probar como
óptimo a tiempo, vs. ~74-85% con el margen generoso).

---

## 3. Etapa B — Empaquetado 3D por posición

### 3.1 Contorno del pallet (bandas)

Un pallet real de carga aérea no es una caja recta: junto al fuselaje (curvo)
la altura de estiba se recorta, y se mantiene completa hacia el pasillo o la
quilla central. Cada `PosicionCarga` modela esto como una lista de **bandas**
transversales — franjas del pallet, cada una con su propio ancho y su propia
altura máxima:

```
banda_k = (ancho_k, alto_k, offset_y_k)      k = 1..K
V_p = Σ_k  largo_p · ancho_k · alto_k
```

Una posición junto al fuselaje (`lado = izquierdo/derecho`) usa 2 bandas
(borde exterior bajo, interior alto); una posición central en una sola fila
(`lado = centro`) usa 3 (bajo–alto–bajo, simétrica). El resultado se ve como
un perfil escalonado en vez de una caja pareja — una aproximación razonable
del contorno real sin necesitar geometría curva exacta.

### 3.2 Empaquetado y apilamiento

Para cada posición `p`, con el conjunto de paquetes que la Etapa A le asignó:

1. Cada banda del contorno se modela como un contenedor (`Bin` de `py3dbp`)
   independiente, con sus propias dimensiones `(largo_p, ancho_k, alto_k)` y
   la capacidad de peso `W_p` de la posición.
2. Cada paquete asignado se modela como una caja (`Item`) con sus
   dimensiones `(largo_i, ancho_i, alto_i)` y peso `peso_i`, y se reparten
   entre bandas en este orden: primero los paquetes apilables (de mayor a
   menor densidad de valor `ingreso_i / vol_i`), y al final los no apilables
   o de alto riesgo. Colocar primero lo apilable arma una base sólida sobre
   la que se puede seguir apilando; dejar lo no apilable para el final evita
   que ocupe temprano posiciones "de crecimiento" y bloquee el apilamiento
   de todo lo que se coloca después. La primera banda recibe lo que le
   quepa, el resto pasa a la siguiente.
3. Dentro de cada banda se corre una heurística de bin-packing 3D (misma
   lógica de pivotes esquina-a-esquina de `py3dbp`), pero con una restricción
   adicional: **nada puede quedar apoyado sobre un paquete con
   `apilable = false` o `riesgo_alto = true`.** Se verifica en ambos
   sentidos — un paquete no se coloca encima de uno no apilable ya puesto, y
   un paquete no apilable tampoco se coloca justo debajo de algo que ya
   estaba ahí (los paquetes no se procesan por altura, sino por densidad de
   valor, así que puede llegar en cualquier orden).
4. Si una caja no logra ubicarse en ninguna banda (el volumen agregado
   cabía, pero la geometría o la restricción de apilamiento no lo permitió),
   queda reportada como no colocada.

`riesgo_alto` implica siempre `apilable = false` (la carga de alto riesgo
nunca admite nada encima), y además se marca de forma distinta en la
visualización 3D.

El resultado final es, por avión: qué paquetes viajan, en qué pallet, en qué
coordenadas dentro del pallet — listo para visualizar en 3D.

### 3.3 Límite conocido de esta aproximación

La heurística de `py3dbp` no simula gravedad ni contacto físico real: dos
cajas pueden coincidir en la misma altura por caminos de colocación
distintos sin que haya, en sentido estricto, una que "sostenga" a la otra.
El chequeo de apilamiento de esta POC filtra los casos donde de verdad hay
solape de huella (x, y) y contacto exacto en altura, que cubre el caso
práctico relevante, pero no reemplaza una validación de estabilidad física
completa.

### 3.4 Costo real de la carga no apilable

Mientras más carga no apilable/alto riesgo tiene el catálogo, más volumen se
desperdicia — es un efecto físico real, no un defecto del modelo: si una
fracción de las cajas no admite nada encima, queda más "aire" atrapado sobre
ellas. Con un catálogo de referencia (1650 cajas, B767F), el volumen
realmente aprovechado —después del empaquetado 3D, no la promesa agregada
de la Etapa A— cayó de ~75% con 0% de carga no apilable a ~64% con 11% no
apilable + 6% alto riesgo, y a ~50% con 30% + 15%. El orden de empaquetado
descrito en 3.2 (apilables primero) recupera buena parte de esa pérdida
(~64% → ~74% en el mismo catálogo de 11%+6%), pero no la elimina — es
inherente a que una porción de la carga no se puede usar como base de
apilamiento.

---

## 4. KPIs reportados

- **Ingreso total** obtenido vs. ingreso potencial del catálogo completo.
- **Utilización de peso** por posición y del avión (%).
- **Utilización de volumen** por posición (%).
- **Centro de gravedad resultante** vs. rango admisible.
- **Paquetes no embarcados** (y motivo: peso, volumen o balance).

## 5. Próximas extensiones (fuera de esta POC)

Ver la sección "Hoja de ruta" del [`README.md`](../README.md) para el detalle
completo y en qué orden. Resumen técnico de lo que falta:

- Aviones de pasajeros con **belly cargo**: capacidad variable (no fija) y
  contenedores tipo LD3/LD6 en vez de pallets — el mecanismo de
  `factor_disponibilidad` (sección 2, restricciones (2)-(4)) ya cubre la
  parte de "capacidad reducida/variable", falta la geometría de contenedor
  distinta.
- Multi-avión / multi-ruta simultánea: optimizar toda la fila de aviones a
  la vez (hoy es secuencial y greedy, ver sección 2.1) en vez de avión por
  avión.
- Restricciones de compatibilidad adicionales (carga refrigerada, IATA
  hazmat, incompatibilidad entre productos — no apilable/riesgo alto ya
  están implementados, ver sección 3.2).
- Balance lateral (izquierdo/derecho) además del longitudinal — la
  restricción (5) de CG hoy solo considera el brazo a lo largo del fuselaje.
- Función objetivo multi-criterio (ingreso, prioridad de cliente,
  perecibilidad).
- Reoptimización dinámica cuando llega carga de último minuto (*late tender*).
- Empaquetado 3D con optimización real (no heurística) usando CP-SAT /
  OR-Tools — ver `.claude/skills/comparar-solvers/` para la comparativa ya
  hecha de solvers para la Etapa A (selección), que sirve de referencia.
