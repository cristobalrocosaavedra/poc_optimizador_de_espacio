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
Σ_i peso_i · x_{i,p}  ≤  W_p     ∀ p
```

**(3) Capacidad de volumen por posición (con margen de seguridad):**
```
Σ_i vol_i · x_{i,p}  ≤  f_seg · V_p     ∀ p
```

**(4) Payload máximo total del avión:**
```
Σ_p Σ_i peso_i · x_{i,p}  ≤  PesoMax_avión
```

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

---

## 3. Etapa B — Empaquetado 3D por posición

Para cada posición `p`, con el conjunto de paquetes que la Etapa A le asignó:

1. Se define un contenedor (`Bin`) con las dimensiones físicas de la
   posición `(largo_p, ancho_p, alto_p)` y su capacidad de peso `W_p`.
2. Cada paquete asignado se modela como una caja (`Item`) con sus
   dimensiones `(largo_i, ancho_i, alto_i)` y peso `peso_i`.
3. Se corre el algoritmo de bin-packing 3D (`py3dbp`, heurística *guillotine
   + best-fit*) para obtener la posición `(x, y, z)` de cada caja dentro
   del pallet.
4. Si el algoritmo no logra ubicar alguna caja (el volumen agregado cabía,
   pero la geometría no), esa caja se remueve empezando por la de menor
   `ingreso_i / vol_i` (peor "densidad de valor") y se reintenta.

El resultado final es, por avión: qué paquetes viajan, en qué pallet, en qué
coordenadas dentro del pallet — listo para visualizar en 3D.

---

## 4. KPIs reportados

- **Ingreso total** obtenido vs. ingreso potencial del catálogo completo.
- **Utilización de peso** por posición y del avión (%).
- **Utilización de volumen** por posición (%).
- **Centro de gravedad resultante** vs. rango admisible.
- **Paquetes no embarcados** (y motivo: peso, volumen o balance).

## 5. Próximas extensiones (fuera de esta POC)

- Multi-avión / multi-ruta simultánea (asignar carga entre varios vuelos).
- Restricciones de compatibilidad (carga refrigerada, IATA hazmat, no apilable).
- Función objetivo multi-criterio (ingreso, prioridad de cliente, perecibilidad).
- Reoptimización dinámica cuando llega carga de último minuto (*late tender*).
- Empaquetado 3D con optimización real (no heurística) usando CP-SAT / OR-Tools.
