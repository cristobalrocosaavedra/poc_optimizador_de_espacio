"""Generador de datos simulados: temporada alta de exportación de flores.

Los tipos de caja y rangos de peso están inspirados en estándares reales de
exportación floricultora (QB/HB/FB), pero los números son simulados para
efectos de esta prueba de concepto.
"""

from __future__ import annotations

import random

from .entidades import Avion, BandaAltura, Paquete, PosicionCarga

# Tipos de caja estándar de exportación de flores: (nombre, largo, ancho, alto en cm)
TIPOS_CAJA = {
    "QB": (48.0, 25.0, 21.0),   # Quarter Box
    "HB": (100.0, 25.0, 21.0),  # Half / Hawaiian Box
    "FB": (100.0, 50.0, 21.0),  # Full Box
}

VARIEDADES_FLORES = [
    "Rosas", "Claveles", "Hortensias", "Alstroemerias", "Crisantemos", "Gypsophila",
]

DESTINOS = ["Miami", "Amsterdam", "Houston", "Madrid"]

CLIENTES = [
    "Floricultora Andina", "Agroexport Sabana", "Flores del Valle", "Jardines Ecuador",
    "Flowers Group SA", "Bogota Blooms", "Quito Flower Co",
]

# Ingreso simulado por kg según variedad (USD/kg), más alto en temporada peak (San Valentín).
TARIFA_USD_KG = {
    "Rosas": (3.4, 4.6),
    "Claveles": (2.2, 3.0),
    "Hortensias": (3.0, 4.0),
    "Alstroemerias": (2.4, 3.2),
    "Crisantemos": (2.0, 2.8),
    "Gypsophila": (2.6, 3.6),
}

# Densidad simulada (kg / m3) por tipo de caja para generar pesos realistas.
DENSIDAD_KG_M3 = {"QB": 140.0, "HB": 130.0, "FB": 120.0}

# Separación entre los pallets izquierdo/derecho de una misma estación (pasillo/quilla central).
GAP_PASILLO_CM = 30.0


def generar_catalogo_paquetes(
    n: int = 120,
    seed: int = 42,
    pct_obligatorio: float = 0.1,
    pct_no_apilable: float = 0.15,
    pct_riesgo_alto: float = 0.06,
) -> list[Paquete]:
    """Simula un catálogo de cajas de flores disponibles para un vuelo.

    Parameters
    ----------
    n: número de paquetes a generar.
    seed: semilla para reproducibilidad.
    pct_obligatorio: fracción de paquetes con embarque garantizado por contrato.
    pct_no_apilable: fracción de cajas sobre las que no se puede apoyar otra caja.
    pct_riesgo_alto: fracción de carga de alto riesgo/frágil (siempre no apilable,
        y se prioriza dejarla en una posición accesible, no enterrada bajo otras).
    """
    rng = random.Random(seed)
    paquetes: list[Paquete] = []

    for idx in range(n):
        tipo_caja = rng.choice(list(TIPOS_CAJA.keys()))
        largo, ancho, alto = TIPOS_CAJA[tipo_caja]
        variedad = rng.choice(VARIEDADES_FLORES)
        destino = rng.choice(DESTINOS)
        cliente = rng.choice(CLIENTES)

        volumen_m3 = (largo * ancho * alto) / 1_000_000.0
        densidad = DENSIDAD_KG_M3[tipo_caja] * rng.uniform(0.9, 1.1)
        peso_kg = round(volumen_m3 * densidad, 2)

        tarifa_min, tarifa_max = TARIFA_USD_KG[variedad]
        tarifa = rng.uniform(tarifa_min, tarifa_max)
        # Prima adicional aleatoria por destino/cliente premium.
        prima_destino = rng.uniform(0.95, 1.15)
        ingreso_usd = round(peso_kg * tarifa * prima_destino, 2)

        obligatorio = rng.random() < pct_obligatorio
        riesgo_alto = rng.random() < pct_riesgo_alto
        apilable = (not riesgo_alto) and (rng.random() >= pct_no_apilable)

        paquetes.append(
            Paquete(
                id=f"PKG-{idx + 1:04d}-{tipo_caja}",
                tipo_producto=variedad,
                cliente=cliente,
                destino=destino,
                peso_kg=peso_kg,
                largo_cm=largo,
                ancho_cm=ancho,
                alto_cm=alto,
                ingreso_usd=ingreso_usd,
                obligatorio=obligatorio,
                apilable=apilable,
                riesgo_alto=riesgo_alto,
            )
        )

    return paquetes


def generar_bandas_contorno(ancho_cm: float, alto_max_cm: float, lado: str) -> list[BandaAltura]:
    """Genera el perfil de altura (contorno) de un pallet según su ubicación.

    El fuselaje del avión es curvo, así que la altura de estiba se recorta
    hacia la pared exterior (junto al fuselaje) y se mantiene completa hacia
    el pasillo/quilla central. `lado="centro"` genera un perfil simétrico
    (recortado en ambos bordes), usado en posiciones de una sola fila.
    """
    factor_recorte = 0.55  # altura utilizable junto al fuselaje, como fracción del máximo

    if lado == "centro":
        ext = ancho_cm * 0.26
        centro = ancho_cm - 2 * ext
        return [
            BandaAltura(ext, alto_max_cm * factor_recorte, 0.0),
            BandaAltura(centro, alto_max_cm, ext),
            BandaAltura(ext, alto_max_cm * factor_recorte, ext + centro),
        ]

    exterior = ancho_cm * 0.38
    interior = ancho_cm - exterior
    if lado == "izquierdo":
        # El borde junto al fuselaje queda al inicio de la franja (offset 0).
        return [
            BandaAltura(exterior, alto_max_cm * factor_recorte, 0.0),
            BandaAltura(interior, alto_max_cm, exterior),
        ]
    if lado == "derecho":
        # El borde junto al fuselaje queda al final de la franja.
        return [
            BandaAltura(interior, alto_max_cm, 0.0),
            BandaAltura(exterior, alto_max_cm * factor_recorte, interior),
        ]
    raise ValueError(f"lado no soportado: {lado}")


def _estacion_par(
    estacion: int, brazo_m: float, ancho_pallet: float, largo_cm: float,
    alto_max_cm: float, peso_max_kg: float,
) -> list[PosicionCarga]:
    """Crea un par de pallets (izquierdo/derecho) para una misma estación longitudinal,
    separados por el pasillo/quilla central — así es como se cargan de verdad la
    mayoría de los aviones de carga anchos: no una sola fila, sino dos hileras."""
    izquierdo = PosicionCarga(
        id=f"PLT-{estacion}-IZQ", peso_max_kg=peso_max_kg, largo_cm=largo_cm,
        ancho_cm=ancho_pallet, alto_max_cm=alto_max_cm, brazo_m=brazo_m,
        estacion=estacion, lado="izquierdo", y_offset_cm=0.0,
        bandas=generar_bandas_contorno(ancho_pallet, alto_max_cm, "izquierdo"),
    )
    derecho = PosicionCarga(
        id=f"PLT-{estacion}-DER", peso_max_kg=peso_max_kg, largo_cm=largo_cm,
        ancho_cm=ancho_pallet, alto_max_cm=alto_max_cm, brazo_m=brazo_m,
        estacion=estacion, lado="derecho", y_offset_cm=ancho_pallet + GAP_PASILLO_CM,
        bandas=generar_bandas_contorno(ancho_pallet, alto_max_cm, "derecho"),
    )
    return [izquierdo, derecho]


def crear_avion(modelo: str = "B767F") -> Avion:
    """Crea un avión de carga con posiciones de pallet predefinidas.

    Modelos disponibles:
    - "B767F" (widebody): 6 estaciones x 2 pallets (izquierdo/derecho) = 12 posiciones,
      cada una con contorno recortado hacia el fuselaje.
    - "B737F" (narrowbody): 5 posiciones en una sola fila central, con contorno
      simétrico recortado en ambos bordes.
    """
    if modelo == "B767F":
        brazos = [4.5, 8.5, 12.5, 16.5, 20.5, 24.5]
        posiciones: list[PosicionCarga] = []
        for i, brazo in enumerate(brazos, start=1):
            alto = 120.0 if i in (1, len(brazos)) else 160.0  # nariz/cola más bajas
            posiciones.extend(
                _estacion_par(i, brazo, ancho_pallet=150.0, largo_cm=300.0, alto_max_cm=alto, peso_max_kg=2600.0)
            )
        return Avion(
            id="AC-767F-01",
            modelo="Boeing 767-300F",
            posiciones=posiciones,
            peso_max_carga_kg=52_000.0,
            cg_min_m=13.0,
            cg_max_m=18.0,
        )

    if modelo == "B737F":
        brazos = [3.5, 6.0, 8.5, 11.0, 13.5]
        posiciones = [
            PosicionCarga(
                id=f"PLT-{i + 1}",
                peso_max_kg=2200.0,
                largo_cm=224.0,
                ancho_cm=153.0,
                alto_max_cm=140.0,
                brazo_m=brazo,
                estacion=i + 1,
                lado="centro",
                y_offset_cm=0.0,
                bandas=generar_bandas_contorno(153.0, 140.0, "centro"),
            )
            for i, brazo in enumerate(brazos)
        ]
        return Avion(
            id="AC-737F-01",
            modelo="Boeing 737-800F",
            posiciones=posiciones,
            peso_max_carga_kg=10_000.0,
            cg_min_m=7.0,
            cg_max_m=10.0,
        )

    raise ValueError(f"Modelo de avión no soportado: {modelo}")


def flota_temporada_flores() -> list[Avion]:
    """Flota de ejemplo para la ruta de temporada de flores (2-3 aviones)."""
    return [
        crear_avion("B767F"),
        crear_avion("B767F"),
        crear_avion("B737F"),
    ]
