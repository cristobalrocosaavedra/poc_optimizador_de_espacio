"""Etapa B: empaquetado geométrico 3D dentro de cada posición de carga.

Usa py3dbp (heurística de bin-packing 3D) para calcular la posición (x, y, z)
de cada caja dentro del pallet, y valida que lo que la Etapa A asignó por
peso/volumen efectivamente entre en la geometría real del pallet.
"""

from __future__ import annotations

from dataclasses import dataclass

from py3dbp import Bin, Item, Packer

from .entidades import Paquete, PosicionCarga


@dataclass
class CajaColocada:
    paquete: Paquete
    x_cm: float
    y_cm: float
    z_cm: float
    largo_cm: float
    ancho_cm: float
    alto_cm: float


@dataclass
class ResultadoEmpaque:
    posicion: PosicionCarga
    colocadas: list[CajaColocada]
    no_colocadas: list[Paquete]


def _dimensiones_rotadas(item: Item) -> tuple[float, float, float]:
    """Dimensiones (largo, ancho, alto) de un Item de py3dbp tras su rotación."""
    w, h, d = item.get_dimension()
    return float(w), float(h), float(d)


def empaquetar_posicion(
    posicion: PosicionCarga, paquetes: list[Paquete]
) -> ResultadoEmpaque:
    """Empaqueta los paquetes asignados a una posición usando bin-packing 3D real.

    Se intenta un único empaquetado, priorizando (orden de carga) los
    paquetes de mayor densidad de valor (ingreso / m3). Si alguno no cabe
    geométricamente (raro, dado el margen de seguridad de la Etapa A),
    queda reportado en `no_colocadas` — sin reintentos caja por caja, que
    para pallets con muchos paquetes puede volverse extremadamente lento
    (cada reintento vuelve a empaquetar todo desde cero).
    """
    if not paquetes:
        return ResultadoEmpaque(posicion=posicion, colocadas=[], no_colocadas=[])

    ordenados = sorted(paquetes, key=lambda p: p.densidad_valor, reverse=True)

    packer = Packer()
    bin_ = Bin(
        posicion.id,
        posicion.largo_cm,
        posicion.ancho_cm,
        posicion.alto_max_cm,
        posicion.peso_max_kg,
    )
    packer.add_bin(bin_)
    for paq in ordenados:
        packer.add_item(
            Item(paq.id, paq.largo_cm, paq.ancho_cm, paq.alto_cm, paq.peso_kg)
        )
    packer.pack(bigger_first=False, distribute_items=False, number_of_decimals=1)

    bin_resultado = packer.bins[0]
    paquetes_por_id = {p.id: p for p in ordenados}

    colocadas = []
    for item in bin_resultado.items:
        paq = paquetes_por_id[item.name]
        x, y, z = (float(c) for c in item.position)
        largo, ancho, alto = _dimensiones_rotadas(item)
        colocadas.append(
            CajaColocada(
                paquete=paq,
                x_cm=x,
                y_cm=y,
                z_cm=z,
                largo_cm=largo,
                ancho_cm=ancho,
                alto_cm=alto,
            )
        )

    ids_colocados = {c.paquete.id for c in colocadas}
    no_colocadas = [p for p in ordenados if p.id not in ids_colocados]

    return ResultadoEmpaque(posicion=posicion, colocadas=colocadas, no_colocadas=no_colocadas)
