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

    @property
    def volumen_m3(self) -> float:
        return (self.largo_cm * self.ancho_cm * self.alto_cm) / 1_000_000.0

    @property
    def densidad_valor(self) -> float:
        """Ingreso por m³, usado para decidir qué descartar si algo no cabe."""
        vol = self.volumen_m3
        return self.ingreso_usd / vol if vol > 0 else 0.0


@dataclass
class PosicionCarga:
    """Una posición de pallet/ULD dentro del avión."""

    id: str
    peso_max_kg: float
    largo_cm: float
    ancho_cm: float
    alto_max_cm: float
    brazo_m: float  # brazo de momento respecto a la referencia del avión

    @property
    def volumen_max_m3(self) -> float:
        return (self.largo_cm * self.ancho_cm * self.alto_max_cm) / 1_000_000.0


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
