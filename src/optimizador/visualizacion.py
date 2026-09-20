"""Visualización 3D (Plotly) de pallets y aviones cargados."""

from __future__ import annotations

import numpy as np
import plotly.graph_objects as go

from .empaquetado_3d import ResultadoEmpaque
from .entidades import BandaAltura, Paquete

PALETA_PRODUCTOS = {
    "Rosas": "#e05263",
    "Claveles": "#f4a261",
    "Hortensias": "#7b8cde",
    "Alstroemerias": "#f7c548",
    "Crisantemos": "#8fbf6b",
    "Gypsophila": "#c9c9d4",
}

COLOR_FUSELAJE = "#aab4c2"
COLOR_PISO = "#8d95a3"
COLOR_RIESGO = "#d7263d"
COLOR_NO_APILABLE = "#e08a00"

CAMARA_DEFECTO = dict(eye=dict(x=1.6, y=-1.7, z=0.9), up=dict(x=0, y=0, z=1))


def _cubo_mesh(
    x0: float, y0: float, z0: float, dx: float, dy: float, dz: float, color: str, texto: str
) -> go.Mesh3d:
    """Construye un cubo sólido (Mesh3d) ubicado en (x0,y0,z0) con dimensiones (dx,dy,dz)."""
    xs = [x0, x0, x0 + dx, x0 + dx, x0, x0, x0 + dx, x0 + dx]
    ys = [y0, y0 + dy, y0 + dy, y0, y0, y0 + dy, y0 + dy, y0]
    zs = [z0, z0, z0, z0, z0 + dz, z0 + dz, z0 + dz, z0 + dz]
    i = [0, 0, 0, 1, 1, 2, 4, 4, 4, 5, 5, 6]
    j = [1, 2, 4, 2, 5, 6, 5, 6, 0, 6, 1, 7]
    k = [2, 3, 5, 6, 6, 7, 6, 7, 1, 7, 2, 3]
    return go.Mesh3d(
        x=xs, y=ys, z=zs, i=i, j=j, k=k,
        color=color, opacity=1.0, flatshading=True,
        hovertext=texto, hoverinfo="text",
        name=texto,
    )


def _contorno_caja(x0, y0, z0, dx, dy, dz, color="#333", width=3) -> list[go.Scatter3d]:
    """Wireframe de una caja (pallet, banda de contorno, o avión) de referencia."""
    v = [
        (x0, y0, z0), (x0 + dx, y0, z0), (x0 + dx, y0 + dy, z0), (x0, y0 + dy, z0), (x0, y0, z0),
        (x0, y0, z0 + dz), (x0 + dx, y0, z0 + dz), (x0 + dx, y0 + dy, z0 + dz), (x0, y0 + dy, z0 + dz),
        (x0, y0, z0 + dz),
    ]
    aristas_verticales = [
        [(x0 + dx, y0, z0), (x0 + dx, y0, z0 + dz)],
        [(x0 + dx, y0 + dy, z0), (x0 + dx, y0 + dy, z0 + dz)],
        [(x0, y0 + dy, z0), (x0, y0 + dy, z0 + dz)],
    ]
    xs, ys, zs = zip(*v)
    trazos = [go.Scatter3d(x=xs, y=ys, z=zs, mode="lines", line=dict(color=color, width=width), showlegend=False, hoverinfo="skip")]
    for arista in aristas_verticales:
        xa, ya, za = zip(*arista)
        trazos.append(
            go.Scatter3d(x=xa, y=ya, z=za, mode="lines", line=dict(color=color, width=width), showlegend=False, hoverinfo="skip")
        )
    return trazos


def _contorno_pallet(x0: float, y0: float, bandas: list[BandaAltura], largo_cm: float, color="#555", width=2) -> list[go.Scatter3d]:
    """Wireframe del pallet completo, banda por banda — dibuja el contorno real
    (recortado hacia el fuselaje) en vez de una caja rectangular pareja."""
    trazos = []
    for banda in bandas:
        trazos.extend(
            _contorno_caja(x0, y0 + banda.offset_y_cm, 0, largo_cm, banda.ancho_cm, banda.alto_cm, color=color, width=width)
        )
    return trazos


def _placa_pallet(x0: float, y0: float, largo: float, ancho: float, z: float = 0.0) -> go.Mesh3d:
    """Base metálica del pallet (la plancha de aluminio real de un ULD/pallet aéreo)."""
    xs = [x0, x0 + largo, x0 + largo, x0]
    ys = [y0, y0, y0 + ancho, y0 + ancho]
    zs = [z, z, z, z]
    return go.Mesh3d(
        x=xs, y=ys, z=zs, i=[0, 0], j=[1, 2], k=[2, 3],
        color=COLOR_PISO, opacity=0.75, flatshading=True,
        hoverinfo="skip", showlegend=False, name="Base pallet",
    )


def _marcadores_especiales(puntos: list[tuple[float, float, float, Paquete]]) -> list[go.Scatter3d]:
    """Marca carga de alto riesgo / no apilable con un símbolo flotante sobre la caja.

    Se agrupan TODOS los puntos en, como máximo, dos trazas (una por categoría) en
    vez de una traza por caja — con miles de cajas, una traza por caja vuelve el
    gráfico lento de rotar/zoomear en el navegador.
    """
    riesgo = [(x, y, z, p) for x, y, z, p in puntos if p.riesgo_alto]
    no_apilable = [(x, y, z, p) for x, y, z, p in puntos if not p.riesgo_alto and not p.apilable]

    trazos = []
    if riesgo:
        xs, ys, zs, ps = zip(*riesgo)
        trazos.append(
            go.Scatter3d(
                x=xs, y=ys, z=zs, mode="markers",
                marker=dict(size=4, color=COLOR_RIESGO, symbol="diamond"),
                hovertext=[f"⚠ {p.id} — alto riesgo (no apilable)" for p in ps],
                hoverinfo="text", name="Alto riesgo",
            )
        )
    if no_apilable:
        xs, ys, zs, ps = zip(*no_apilable)
        trazos.append(
            go.Scatter3d(
                x=xs, y=ys, z=zs, mode="markers",
                marker=dict(size=3.5, color=COLOR_NO_APILABLE, symbol="circle"),
                hovertext=[f"🚫 {p.id} — no apilable" for p in ps],
                hoverinfo="text", name="No apilable",
            )
        )
    return trazos


def _fuselaje(
    largo_total: float,
    y_centro: float,
    radio: float,
    margen_nariz: float,
    margen_cola: float,
    n_theta: int = 28,
    n_x: int = 36,
) -> go.Surface:
    """Tubo del fuselaje (con nariz y cola achatadas) envolviendo la zona de carga.

    Se modela como un cilindro cuyo piso de carga (z=0) queda como una cuerda baja
    del círculo transversal, y cuyo radio se angosta suavemente en los extremos
    para sugerir la nariz y la cola del avión.
    """
    x_ini = -margen_nariz
    x_fin = largo_total + margen_cola
    xs = np.linspace(x_ini, x_fin, n_x)
    thetas = np.linspace(0, 2 * np.pi, n_theta)

    def factor_ahusado(x: float) -> float:
        if x < 0:
            t = np.clip(x / -margen_nariz, 0.0, 1.0) if margen_nariz else 1.0
            return 0.15 + 0.85 * (1 - (1 - t) ** 2.2)
        if x > largo_total:
            t = np.clip((x - largo_total) / margen_cola, 0.0, 1.0) if margen_cola else 1.0
            return 0.15 + 0.85 * (1 - t**2.2)
        return 1.0

    # El piso de carga (z=0) queda como una cuerda baja del círculo transversal
    # (no su punto más bajo), como en un avión real donde el piso de bodega no
    # pasa por el fondo exacto del fuselaje. coef_piso controla esa proporción,
    # y se mantiene constante al angostar nariz/cola para que la sección se vea
    # consistente en todo el largo.
    coef_piso = 0.55
    X = np.tile(xs.reshape(-1, 1), (1, n_theta))
    R = np.array([radio * factor_ahusado(x) for x in xs]).reshape(-1, 1)
    Z_centro = coef_piso * R
    Y = y_centro + R * np.cos(thetas).reshape(1, -1)
    Z = Z_centro + R * np.sin(thetas).reshape(1, -1)

    return go.Surface(
        x=X, y=Y, z=Z,
        colorscale=[[0, COLOR_FUSELAJE], [1, COLOR_FUSELAJE]],
        showscale=False, opacity=0.35,
        lighting=dict(ambient=0.95, diffuse=0.2, specular=0.05, roughness=0.9),
        contours=dict(
            x=dict(show=True, color="#7c8798", width=1),
            y=dict(show=False),
            z=dict(show=False),
        ),
        hoverinfo="skip", name="Fuselaje",
    )


def _piso_carga(x_ini: float, x_fin: float, y_ini: float, y_fin: float) -> go.Mesh3d:
    """Piso plano de la bodega de carga, de referencia visual."""
    xs = [x_ini, x_fin, x_fin, x_ini]
    ys = [y_ini, y_ini, y_fin, y_fin]
    zs = [0, 0, 0, 0]
    return go.Mesh3d(
        x=xs, y=ys, z=zs, i=[0, 0], j=[1, 2], k=[2, 3],
        color=COLOR_PISO, opacity=0.35, flatshading=True,
        hoverinfo="skip", showlegend=False, name="Piso de carga",
    )


def figura_posicion(resultado: ResultadoEmpaque) -> go.Figure:
    """Figura 3D de una sola posición de pallet con sus cajas colocadas."""
    pos = resultado.posicion
    fig = go.Figure()
    fig.add_trace(_placa_pallet(0, 0, pos.largo_cm, pos.ancho_cm))
    for trazo in _contorno_pallet(0, 0, pos.bandas, pos.largo_cm):
        fig.add_trace(trazo)

    puntos_especiales = []
    for caja in resultado.colocadas:
        color = PALETA_PRODUCTOS.get(caja.paquete.tipo_producto, "#999999")
        texto = (
            f"{caja.paquete.id}<br>{caja.paquete.tipo_producto} · {caja.paquete.cliente}"
            f"<br>{caja.paquete.peso_kg} kg · ${caja.paquete.ingreso_usd:,.0f}"
            f"<br>{'apilable' if caja.paquete.apilable else 'NO apilable'}"
            f"{' · ⚠ alto riesgo' if caja.paquete.riesgo_alto else ''}"
        )
        fig.add_trace(
            _cubo_mesh(
                caja.x_cm, caja.y_cm, caja.z_cm,
                caja.largo_cm, caja.ancho_cm, caja.alto_cm,
                color, texto,
            )
        )
        puntos_especiales.append(
            (caja.x_cm + caja.largo_cm / 2, caja.y_cm + caja.ancho_cm / 2, caja.z_cm + caja.alto_cm + 3, caja.paquete)
        )

    for trazo in _marcadores_especiales(puntos_especiales):
        fig.add_trace(trazo)

    fig.update_layout(
        scene=dict(
            xaxis_title="Largo (cm)",
            yaxis_title="Ancho (cm)",
            zaxis_title="Alto (cm)",
            aspectmode="data",
            camera=CAMARA_DEFECTO,
        ),
        margin=dict(l=0, r=0, t=30, b=0),
        legend=dict(itemsizing="constant"),
        title=f"Posición {pos.id} — {len(resultado.colocadas)} cajas (contorno: {pos.lado})",
    )
    return fig


def figura_avion(
    resultados_por_posicion: list[ResultadoEmpaque], separacion_cm: float = 40.0
) -> go.Figure:
    """Figura 3D con todas las posiciones del avión distribuidas a lo largo del
    fuselaje, agrupadas por estación (izquierdo/derecho comparten la misma
    posición longitudinal), envueltas en un fuselaje esquemático con nariz,
    cola y contorno de pallet realista."""
    fig = go.Figure()

    estaciones: dict[int, list[ResultadoEmpaque]] = {}
    for r in resultados_por_posicion:
        estaciones.setdefault(r.posicion.estacion, []).append(r)

    ancho_seccion = max(r.posicion.y_offset_cm + r.posicion.ancho_cm for r in resultados_por_posicion)
    alto_max = max(r.posicion.alto_max_cm for r in resultados_por_posicion)

    puntos_especiales: list[tuple[float, float, float, Paquete]] = []
    x_actual = 0.0

    for estacion_id in sorted(estaciones):
        grupo = estaciones[estacion_id]
        largo_estacion = max(r.posicion.largo_cm for r in grupo)

        for resultado in grupo:
            pos = resultado.posicion
            fig.add_trace(_placa_pallet(x_actual, pos.y_offset_cm, pos.largo_cm, pos.ancho_cm))
            for trazo in _contorno_pallet(x_actual, pos.y_offset_cm, pos.bandas, pos.largo_cm, color="#555", width=2):
                fig.add_trace(trazo)

            fig.add_trace(
                go.Scatter3d(
                    x=[x_actual + pos.largo_cm / 2], y=[pos.y_offset_cm + pos.ancho_cm / 2], z=[pos.alto_max_cm + 35],
                    mode="text", text=[pos.id], textfont=dict(size=10, color="#333"),
                    showlegend=False, hoverinfo="skip",
                )
            )

            for caja in resultado.colocadas:
                color = PALETA_PRODUCTOS.get(caja.paquete.tipo_producto, "#999999")
                texto = (
                    f"{caja.paquete.id} · pallet {pos.id}<br>{caja.paquete.tipo_producto}"
                    f"<br>{caja.paquete.peso_kg} kg · ${caja.paquete.ingreso_usd:,.0f}"
                    f"<br>{'apilable' if caja.paquete.apilable else 'NO apilable'}"
                    f"{' · ⚠ alto riesgo' if caja.paquete.riesgo_alto else ''}"
                )
                x0 = x_actual + caja.x_cm
                y0 = pos.y_offset_cm + caja.y_cm
                fig.add_trace(
                    _cubo_mesh(x0, y0, caja.z_cm, caja.largo_cm, caja.ancho_cm, caja.alto_cm, color, texto)
                )
                puntos_especiales.append(
                    (x0 + caja.largo_cm / 2, y0 + caja.ancho_cm / 2, caja.z_cm + caja.alto_cm + 3, caja.paquete)
                )

        x_actual += largo_estacion + separacion_cm

    largo_total = x_actual - separacion_cm
    margen_nariz = max(largo_total * 0.16, 350.0)
    margen_cola = max(largo_total * 0.10, 220.0)
    radio_fuselaje = max(ancho_seccion, alto_max * 1.8) * 0.62

    fig.add_trace(_piso_carga(-margen_nariz * 0.4, largo_total + margen_cola * 0.4, -20, ancho_seccion + 20))
    fig.add_trace(
        _fuselaje(
            largo_total, y_centro=ancho_seccion / 2, radio=radio_fuselaje,
            margen_nariz=margen_nariz, margen_cola=margen_cola,
        )
    )

    for trazo in _marcadores_especiales(puntos_especiales):
        fig.add_trace(trazo)

    # Leyenda manual por tipo de producto (los Mesh3d no generan leyenda limpia).
    for producto, color in PALETA_PRODUCTOS.items():
        fig.add_trace(
            go.Scatter3d(
                x=[None], y=[None], z=[None], mode="markers",
                marker=dict(size=8, color=color), name=producto,
            )
        )

    fig.update_layout(
        scene=dict(
            xaxis_title="Eje longitudinal del avión (cm)",
            yaxis_title="Ancho (cm)",
            zaxis_title="Alto (cm)",
            aspectmode="data",
            camera=CAMARA_DEFECTO,
        ),
        margin=dict(l=0, r=0, t=30, b=0),
        legend=dict(itemsizing="constant"),
        title="Distribución de carga en el avión (contorno real + izquierdo/derecho)",
    )
    return fig
