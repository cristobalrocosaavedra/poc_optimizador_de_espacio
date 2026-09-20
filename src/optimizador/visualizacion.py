"""Visualización 3D (Plotly) de pallets y aviones cargados."""

from __future__ import annotations

import plotly.graph_objects as go

from .empaquetado_3d import CajaColocada, ResultadoEmpaque
from .entidades import PosicionCarga

PALETA_PRODUCTOS = {
    "Rosas": "#e05263",
    "Claveles": "#f4a261",
    "Hortensias": "#7b8cde",
    "Alstroemerias": "#f7c548",
    "Crisantemos": "#8fbf6b",
    "Gypsophila": "#c9c9d4",
}


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


def _contorno_caja(x0, y0, z0, dx, dy, dz) -> go.Scatter3d:
    """Wireframe del contenedor (pallet o avión) para dar referencia de escala."""
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
    trazos = [go.Scatter3d(x=xs, y=ys, z=zs, mode="lines", line=dict(color="#333", width=3), showlegend=False)]
    for arista in aristas_verticales:
        xa, ya, za = zip(*arista)
        trazos.append(go.Scatter3d(x=xa, y=ya, z=za, mode="lines", line=dict(color="#333", width=3), showlegend=False))
    return trazos


def figura_posicion(resultado: ResultadoEmpaque) -> go.Figure:
    """Figura 3D de una sola posición de pallet con sus cajas colocadas."""
    pos = resultado.posicion
    fig = go.Figure()
    for trazo in _contorno_caja(0, 0, 0, pos.largo_cm, pos.ancho_cm, pos.alto_max_cm):
        fig.add_trace(trazo)

    for caja in resultado.colocadas:
        color = PALETA_PRODUCTOS.get(caja.paquete.tipo_producto, "#999999")
        texto = (
            f"{caja.paquete.id}<br>{caja.paquete.tipo_producto} · {caja.paquete.cliente}"
            f"<br>{caja.paquete.peso_kg} kg · ${caja.paquete.ingreso_usd:,.0f}"
        )
        fig.add_trace(
            _cubo_mesh(
                caja.x_cm, caja.y_cm, caja.z_cm,
                caja.largo_cm, caja.ancho_cm, caja.alto_cm,
                color, texto,
            )
        )

    fig.update_layout(
        scene=dict(
            xaxis_title="Largo (cm)",
            yaxis_title="Ancho (cm)",
            zaxis_title="Alto (cm)",
            aspectmode="data",
        ),
        margin=dict(l=0, r=0, t=30, b=0),
        showlegend=False,
        title=f"Posición {pos.id} — {len(resultado.colocadas)} cajas",
    )
    return fig


def figura_avion(
    resultados_por_posicion: list[ResultadoEmpaque], separacion_cm: float = 40.0
) -> go.Figure:
    """Figura 3D con todas las posiciones del avión distribuidas a lo largo del fuselaje."""
    fig = go.Figure()
    x_actual = 0.0

    for resultado in resultados_por_posicion:
        pos = resultado.posicion
        for trazo in _contorno_caja(x_actual, 0, 0, pos.largo_cm, pos.ancho_cm, pos.alto_max_cm):
            fig.add_trace(trazo)

        for caja in resultado.colocadas:
            color = PALETA_PRODUCTOS.get(caja.paquete.tipo_producto, "#999999")
            texto = (
                f"{caja.paquete.id} · pallet {pos.id}<br>{caja.paquete.tipo_producto}"
                f"<br>{caja.paquete.peso_kg} kg · ${caja.paquete.ingreso_usd:,.0f}"
            )
            fig.add_trace(
                _cubo_mesh(
                    x_actual + caja.x_cm, caja.y_cm, caja.z_cm,
                    caja.largo_cm, caja.ancho_cm, caja.alto_cm,
                    color, texto,
                )
            )
        x_actual += pos.largo_cm + separacion_cm

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
        ),
        margin=dict(l=0, r=0, t=30, b=0),
        legend=dict(itemsizing="constant"),
        title="Distribución de carga en el avión",
    )
    return fig
