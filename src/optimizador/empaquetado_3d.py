"""Etapa B: empaquetado geométrico 3D dentro de cada posición de carga.

Usa py3dbp (heurística de bin-packing 3D) para calcular la posición (x, y, z)
de cada caja dentro del pallet, y valida que lo que la Etapa A asignó por
peso/volumen efectivamente entre en la geometría real del pallet — incluido
su contorno (ver `entidades.BandaAltura`) y las restricciones de apilado.
"""

from __future__ import annotations

from dataclasses import dataclass

from py3dbp import Bin, Item

from .entidades import BandaAltura, Paquete, PosicionCarga

# Eje "depth" del Bin de py3dbp = el alto real (vertical) del pallet, dado que
# construimos cada Bin como (largo, ancho_banda, alto_banda). Es el eje que
# hay que restringir para respetar que nada se apoye sobre una caja no apilable.
_EJE_ALTO = 2


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


def _genera_apoyo_invalido(bin_: Bin, item: Item) -> bool:
    """True si colocar `item` deja algo apoyado sobre una caja no apilable.

    Hace falta revisar las dos direcciones, no solo "item queda encima de una
    caja no apilable ya puesta": como los items se van colocando en cualquier
    orden (por densidad de valor, no por altura), una caja no apilable puede
    llegar DESPUÉS y terminar puesta justo debajo de algo que ya estaba ahí —
    en ese caso es la caja recién puesta la que hay que rechazar. También hace
    falta revisar contra TODOS los items que se solapan en x/y a esa altura,
    no solo el que sirvió de pivote: un item ancho puede pivotar desde un
    vecino corto y apilable, y aun así su extensión terminar "puenteando" por
    encima de otro item no apilable vecino de igual altura.
    """
    xi, yi, zi = item.position
    wi, hi, di = item.get_dimension()
    item_no_apilable = not getattr(item, "permite_apilado_encima", True)
    for otro in bin_.items:
        if otro is item:
            continue
        xo, yo, zo = otro.position
        wo, ho, do = otro.get_dimension()
        if not (xi < xo + wo and xo < xi + wi and yi < yo + ho and yo < yi + hi):
            continue  # no se solapan en (x, y): no puede haber apoyo entre ellos
        otro_no_apilable = not getattr(otro, "permite_apilado_encima", True)
        if otro_no_apilable and zi == zo + do:
            return True  # item queda apoyado sobre otro, que no lo permite
        if item_no_apilable and zo == zi + di:
            return True  # otro queda apoyado sobre item, que no lo permite
    return False


def _colocar_item(bin_: Bin, item: Item) -> bool:
    """Intenta colocar un item en el bin probando pivotes junto a cada item ya
    colocado, en los 3 ejes (igual que la heurística de py3dbp), sin permitir
    que nada quede apoyado sobre un item marcado como no apilable."""
    if not bin_.items:
        return bin_.put_item(item, [0, 0, 0])

    for axis in (0, 1, 2):
        for base in bin_.items:
            if axis == _EJE_ALTO and not getattr(base, "permite_apilado_encima", True):
                continue
            w, h, d = base.get_dimension()
            if axis == 0:
                pivot = [base.position[0] + w, base.position[1], base.position[2]]
            elif axis == 1:
                pivot = [base.position[0], base.position[1] + h, base.position[2]]
            else:
                pivot = [base.position[0], base.position[1], base.position[2] + d]
            if bin_.put_item(item, pivot):
                if _genera_apoyo_invalido(bin_, item):
                    bin_.items.remove(item)
                    continue
                return True
    return False


def _bins_por_banda(posicion: PosicionCarga) -> list[tuple[Bin, BandaAltura]]:
    """Un Bin de py3dbp por cada banda de contorno del pallet."""
    resultado = []
    for idx, banda in enumerate(posicion.bandas):
        bin_ = Bin(
            f"{posicion.id}-B{idx}", posicion.largo_cm, banda.ancho_cm, banda.alto_cm,
            posicion.peso_max_kg,
        )
        # py3dbp opera internamente en Decimal (ver Bin.get_total_weight); sin este
        # formateo, sumar el peso (float) de un Item contra el peso máximo (Decimal)
        # revienta con TypeError.
        bin_.format_numbers(1)
        resultado.append((bin_, banda))
    return resultado


def empaquetar_posicion(
    posicion: PosicionCarga, paquetes: list[Paquete]
) -> ResultadoEmpaque:
    """Empaqueta los paquetes asignados a una posición usando bin-packing 3D real.

    El pallet se modela como varias "bandas" transversales (ver
    `entidades.generar_bandas_contorno`), cada una con su propia altura
    utilizable — así se respeta el contorno real del fuselaje. Dentro de cada
    banda, nada puede apoyarse sobre una caja marcada no apilable o de alto
    riesgo.

    Orden de empaquetado: primero las cajas apilables (por densidad de valor
    descendente), y al final las no apilables/alto riesgo. Colocar primero lo
    apilable arma una base sólida sobre la que se puede seguir apilando;
    dejar lo no apilable para el final evita que ocupe temprano posiciones
    "de crecimiento" y bloquee el apilamiento de todo lo que se coloca
    después — probado que reduce bastante el desperdicio de espacio (en un
    caso de referencia, pasar del orden ingenuo por valor a este orden subió
    el volumen realmente aprovechado de ~64% a ~74% con la misma carga).
    """
    if not paquetes:
        return ResultadoEmpaque(posicion=posicion, colocadas=[], no_colocadas=[])

    ordenados = sorted(paquetes, key=lambda p: (not p.permite_apilado_encima, -p.densidad_valor))
    paquetes_por_id = {p.id: p for p in ordenados}

    pendientes: list[Item] = []
    for paq in ordenados:
        item = Item(paq.id, paq.largo_cm, paq.ancho_cm, paq.alto_cm, paq.peso_kg)
        item.format_numbers(1)
        item.permite_apilado_encima = paq.permite_apilado_encima
        pendientes.append(item)

    colocadas: list[CajaColocada] = []
    for bin_, banda in _bins_por_banda(posicion):
        if not pendientes:
            break
        restantes = []
        for item in pendientes:
            if _colocar_item(bin_, item):
                paq = paquetes_por_id[item.name]
                x, y, z = (float(c) for c in item.position)
                largo, ancho, alto = _dimensiones_rotadas(item)
                colocadas.append(
                    CajaColocada(
                        paquete=paq,
                        x_cm=x,
                        y_cm=y + banda.offset_y_cm,
                        z_cm=z,
                        largo_cm=largo,
                        ancho_cm=ancho,
                        alto_cm=alto,
                    )
                )
            else:
                restantes.append(item)
        pendientes = restantes

    ids_colocados = {c.paquete.id for c in colocadas}
    no_colocadas = [p for p in ordenados if p.id not in ids_colocados]

    return ResultadoEmpaque(posicion=posicion, colocadas=colocadas, no_colocadas=no_colocadas)
