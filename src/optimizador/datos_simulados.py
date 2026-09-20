"""Generador de datos simulados: temporada alta de exportación de flores.

Los tipos de caja y rangos de peso están inspirados en estándares reales de
exportación floricultora (QB/HB/FB), pero los números son simulados para
efectos de esta prueba de concepto.
"""

from __future__ import annotations

import random

from .entidades import Avion, Paquete, PosicionCarga

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


def generar_catalogo_paquetes(
    n: int = 120,
    seed: int = 42,
    pct_obligatorio: float = 0.1,
) -> list[Paquete]:
    """Simula un catálogo de cajas de flores disponibles para un vuelo.

    Parameters
    ----------
    n: número de paquetes a generar.
    seed: semilla para reproducibilidad.
    pct_obligatorio: fracción de paquetes con embarque garantizado por contrato.
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
            )
        )

    return paquetes


def _pallet_estandar(id_: str, brazo_m: float, alto_cm: float = 160.0) -> PosicionCarga:
    """Pallet aéreo estándar PMC/P1P (~223 x 317 cm de base)."""
    return PosicionCarga(
        id=id_,
        peso_max_kg=4500.0,
        largo_cm=317.0,
        ancho_cm=223.0,
        alto_max_cm=alto_cm,
        brazo_m=brazo_m,
    )


def crear_avion(modelo: str = "B767F") -> Avion:
    """Crea un avión de carga con posiciones de pallet predefinidas.

    Modelos disponibles: "B767F" (widebody, 10 posiciones) y
    "B737F" (narrowbody, 5 posiciones más pequeñas).
    """
    if modelo == "B767F":
        # Posiciones a lo largo del fuselaje, brazo creciente desde la nariz.
        brazos = [4.0, 6.5, 9.0, 11.5, 14.0, 16.5, 19.0, 21.5, 24.0, 26.5]
        posiciones = [
            _pallet_estandar(f"PLT-{i + 1}", brazo, alto_cm=160.0 if i not in (0, 9) else 120.0)
            for i, brazo in enumerate(brazos)
        ]
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
