"""Entidades del dominio: paquetes, posiciones de carga y aviones."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Paquete:
    """Un bulto de carga a transportar (p.ej. una caja de flores)."""

    id: str
    tipo_producto: str
    cliente: str
    destino: str
    peso_kg: float
    largo_cm: float
    ancho_cm: float
    alto_cm: float
    ingreso_usd: float
    obligatorio: bool = False
    apilable: bool = True
    riesgo_alto: bool = False

    @property
    def volumen_m3(self) -> float:
        return (self.largo_cm * self.ancho_cm * self.alto_cm) / 1_000_000.0

    @property
    def densidad_valor(self) -> float:
        """Ingreso por m³, usado para decidir qué descartar si algo no cabe."""
        vol = self.volumen_m3
        return self.ingreso_usd / vol if vol > 0 else 0.0

    @property
    def permite_apilado_encima(self) -> bool:
        """Si se puede apoyar otra caja encima. La carga de alto riesgo nunca lo permite."""
        return self.apilable and not self.riesgo_alto


@dataclass
class BandaAltura:
    """Una franja transversal del pallet con su propia altura máxima utilizable.

    Modela el "contorno" real de un pallet de carga aérea: junto al fuselaje
    (curvo) la altura de estiba se recorta, mientras que hacia el pasillo o
    quilla central se mantiene la altura completa. `offset_y_cm` es la
    posición de partida de la franja a lo ancho del pallet (0 = un borde).
    """

    ancho_cm: float
    alto_cm: float
    offset_y_cm: float


@dataclass
class PosicionCarga:
    """Una posición de pallet/ULD dentro del avión."""

    id: str
    peso_max_kg: float
    largo_cm: float
    ancho_cm: float
    alto_max_cm: float
    brazo_m: float  # brazo de momento respecto a la referencia del avión (longitudinal)
    estacion: int = 0  # agrupa posiciones que comparten la misma "fila" a lo largo del fuselaje
    lado: str = "centro"  # "izquierdo" | "derecho" | "centro"
    y_offset_cm: float = 0.0  # posición lateral dentro de la sección transversal del avión
    bandas: list[BandaAltura] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.bandas:
            # Sin contorno definido: pallet rectangular simple (compatibilidad).
            self.bandas = [BandaAltura(self.ancho_cm, self.alto_max_cm, 0.0)]

    @property
    def volumen_max_m3(self) -> float:
        return sum(b.ancho_cm * b.alto_cm * self.largo_cm for b in self.bandas) / 1_000_000.0


@dataclass
class Avion:
    """Un avión de carga con sus posiciones de pallet."""

    id: str
    modelo: str
    posiciones: list[PosicionCarga] = field(default_factory=list)
    peso_max_carga_kg: float = 0.0
    cg_min_m: float = 0.0
    cg_max_m: float = 0.0

    @property
    def volumen_total_m3(self) -> float:
        return sum(p.volumen_max_m3 for p in self.posiciones)
