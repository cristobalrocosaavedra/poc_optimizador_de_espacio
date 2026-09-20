"""POC — Optimizador de espacio de carga aérea (temporada de flores).

Ejecutar con:  streamlit run app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import pandas as pd
import streamlit as st

from optimizador.datos_simulados import generar_catalogo_paquetes, crear_avion
from optimizador.empaquetado_3d import empaquetar_posicion
from optimizador.entidades import Paquete
from optimizador.optimizador_carga import optimizar
from optimizador.visualizacion import figura_avion, figura_posicion

st.set_page_config(page_title="Optimizador de carga aérea", layout="wide")

st.title("✈️ Optimizador de espacio de carga — temporada de flores")
st.caption(
    "POC de planificación de carga aérea: selección de paquetes + empaquetado 3D en pallets, "
    "maximizando ingreso sujeto a peso, volumen y balance del avión."
)

with st.expander("📐 Ver formulación matemática del modelo"):
    st.markdown(
        (Path(__file__).parent / "docs" / "formulacion_matematica.md").read_text(encoding="utf-8")
    )

with st.sidebar:
    st.header("Parámetros")
    modelo_avion = st.selectbox("Modelo de avión", ["B767F", "B737F"], index=0)
    n_paquetes = st.slider("Cajas disponibles a simular", 100, 2500, 1100, step=50)
    st.caption(
        "Con pocas cajas entra todo (100%). Sobre ~1400-1500 el avión empieza a saturarse "
        "y el optimizador debe elegir qué dejar en tierra. Catálogos grandes (>1800) tardan más "
        "en el empaquetado 3D real — puede tomar 1-2 minutos."
    )
    seed = st.number_input("Semilla aleatoria", value=42, step=1)
    pct_obligatorio = st.slider("% de cajas con contrato obligatorio", 0, 40, 10) / 100.0
    pct_no_apilable = st.slider(
        "% de cajas no apilables", 0, 50, 15,
        help="Cajas sobre las que no se puede apoyar otra caja (delicadas, top-heavy, etc.).",
    ) / 100.0
    pct_riesgo_alto = st.slider(
        "% de carga de alto riesgo", 0, 30, 6,
        help="Carga frágil/sensible: siempre no apilable, y se marca en el 3D con ⚠.",
    ) / 100.0
    factor_seguridad = st.slider(
        "Factor de seguridad de volumen por pallet", 0.5, 1.0, 0.85, step=0.05,
        help="Margen que deja la Etapa A para que el empaquetado 3D real siempre pueda acomodar la carga.",
    )
    generar = st.button("🔄 Generar carga disponible", width='stretch')

if "df_paquetes" not in st.session_state or generar:
    base = generar_catalogo_paquetes(
        n=n_paquetes, seed=int(seed), pct_obligatorio=pct_obligatorio,
        pct_no_apilable=pct_no_apilable, pct_riesgo_alto=pct_riesgo_alto,
    )
    st.session_state["df_paquetes"] = pd.DataFrame(
        [
            dict(
                id=p.id, producto=p.tipo_producto, cliente=p.cliente, destino=p.destino,
                peso_kg=p.peso_kg, largo_cm=p.largo_cm, ancho_cm=p.ancho_cm, alto_cm=p.alto_cm,
                ingreso_usd=p.ingreso_usd, obligatorio=p.obligatorio,
                apilable=p.apilable, riesgo_alto=p.riesgo_alto,
            )
            for p in base
        ]
    )
    st.session_state.pop("resultado", None)

avion = crear_avion(modelo_avion)

col_izq, col_der = st.columns([1, 1])
with col_izq:
    st.subheader("Catálogo de carga disponible")
    st.caption(
        "Tabla editable: corrige valores, borra filas (selecciona + tecla Supr) o agrega "
        "cajas nuevas con el ➕ al final de la tabla."
    )
    df_editado = st.data_editor(
        st.session_state["df_paquetes"],
        num_rows="dynamic",
        key="editor_paquetes",
        width='stretch',
        height=300,
        column_config={
            "id": st.column_config.TextColumn("ID", help="Déjalo vacío en filas nuevas y se autogenera."),
            "producto": st.column_config.TextColumn("Producto"),
            "cliente": st.column_config.TextColumn("Cliente"),
            "destino": st.column_config.TextColumn("Destino"),
            "peso_kg": st.column_config.NumberColumn("Peso (kg)", min_value=0.01, format="%.2f"),
            "largo_cm": st.column_config.NumberColumn("Largo (cm)", min_value=1.0, format="%.1f"),
            "ancho_cm": st.column_config.NumberColumn("Ancho (cm)", min_value=1.0, format="%.1f"),
            "alto_cm": st.column_config.NumberColumn("Alto (cm)", min_value=1.0, format="%.1f"),
            "ingreso_usd": st.column_config.NumberColumn("Ingreso (USD)", min_value=0.0, format="%.2f"),
            "obligatorio": st.column_config.CheckboxColumn("Obligatorio"),
            "apilable": st.column_config.CheckboxColumn("Apilable"),
            "riesgo_alto": st.column_config.CheckboxColumn("Alto riesgo"),
        },
    )

    paquetes: list[Paquete] = []
    filas_invalidas = 0
    for idx, fila in df_editado.reset_index(drop=True).iterrows():
        try:
            campos_obligatorios = [fila["peso_kg"], fila["largo_cm"], fila["ancho_cm"], fila["alto_cm"], fila["ingreso_usd"]]
            if any(pd.isna(v) for v in campos_obligatorios):
                raise ValueError("faltan campos numéricos")
            id_fila = str(fila["id"]).strip() if pd.notna(fila["id"]) and str(fila["id"]).strip() else f"MANUAL-{idx + 1:04d}"
            paquetes.append(
                Paquete(
                    id=id_fila,
                    tipo_producto=str(fila["producto"]) if pd.notna(fila["producto"]) else "Otro",
                    cliente=str(fila["cliente"]) if pd.notna(fila["cliente"]) else "Cliente manual",
                    destino=str(fila["destino"]) if pd.notna(fila["destino"]) else "Miami",
                    peso_kg=float(fila["peso_kg"]),
                    largo_cm=float(fila["largo_cm"]),
                    ancho_cm=float(fila["ancho_cm"]),
                    alto_cm=float(fila["alto_cm"]),
                    ingreso_usd=float(fila["ingreso_usd"]),
                    obligatorio=bool(fila["obligatorio"]) if pd.notna(fila["obligatorio"]) else False,
                    apilable=bool(fila["apilable"]) if pd.notna(fila["apilable"]) else True,
                    riesgo_alto=bool(fila["riesgo_alto"]) if pd.notna(fila["riesgo_alto"]) else False,
                )
            )
        except (ValueError, TypeError):
            filas_invalidas += 1

    if filas_invalidas:
        st.warning(f"{filas_invalidas} fila(s) con datos incompletos fueron ignoradas (falta peso, dimensiones o ingreso).")

    st.metric("Ingreso potencial (si cupiera todo)", f"${sum(p.ingreso_usd for p in paquetes):,.0f}")

with col_der:
    st.subheader(f"Avión: {avion.modelo}")
    st.write(
        f"**Posiciones de pallet:** {len(avion.posiciones)} "
        f"({len({p.estacion for p in avion.posiciones})} estaciones"
        f"{', izquierdo/derecho' if any(p.lado != 'centro' for p in avion.posiciones) else ''})  \n"
        f"**Payload máximo:** {avion.peso_max_carga_kg:,.0f} kg  \n"
        f"**Volumen total:** {avion.volumen_total_m3:,.1f} m³ (ya descuenta el contorno del fuselaje)  \n"
        f"**Rango CG admisible:** {avion.cg_min_m} – {avion.cg_max_m} m"
    )
    st.caption(
        "Cada pallet tiene su contorno recortado hacia el fuselaje (menos altura útil junto "
        "a la pared) y respeta que nada se apoye sobre carga no apilable o de alto riesgo (⚠)."
    )

st.divider()

if st.button("🚀 Optimizar carga del avión", type="primary"):
    with st.spinner("Resolviendo modelo de asignación (MILP)..."):
        resultado = optimizar(paquetes, avion, factor_seguridad_volumen=factor_seguridad)

    barra = st.progress(0.0, text="Empaquetando en 3D...")
    empaques = []
    for idx, pos in enumerate(avion.posiciones):
        n_asignados = len(resultado.asignacion[pos.id])
        barra.progress(
            idx / len(avion.posiciones),
            text=f"Empaquetando pallet {pos.id} ({idx + 1}/{len(avion.posiciones)}) — {n_asignados} cajas asignadas...",
        )
        empaques.append(empaquetar_posicion(pos, resultado.asignacion[pos.id]))
    barra.progress(1.0, text="Empaquetado 3D completo.")
    barra.empty()
    st.session_state["resultado"] = resultado
    st.session_state["empaques"] = empaques

if "resultado" in st.session_state:
    resultado = st.session_state["resultado"]
    empaques = st.session_state["empaques"]

    if resultado.estado_solver != "Optimal":
        st.warning(f"Estado del solver: {resultado.estado_solver}")

    n_asignados = sum(len(v) for v in resultado.asignacion.values())
    n_colocados_3d = sum(len(e.colocadas) for e in empaques)
    n_no_colocados_3d = sum(len(e.no_colocadas) for e in empaques)
    peso_total = sum(p.peso_kg for e in empaques for p in [c.paquete for c in e.colocadas])

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Ingreso total", f"${resultado.ingreso_total:,.0f}")
    k2.metric(
        "Utilización de ingreso",
        f"{100 * resultado.ingreso_total / resultado.ingreso_potencial:.1f}%"
        if resultado.ingreso_potencial else "—",
    )
    k3.metric("Cajas cargadas", f"{n_colocados_3d} / {len(paquetes)}")
    k4.metric("Peso total cargado", f"{peso_total:,.0f} kg")
    k5.metric(
        "CG resultante",
        f"{resultado.brazo_resultante_m:.2f} m" if resultado.brazo_resultante_m else "—",
    )

    if n_no_colocados_3d:
        st.info(
            f"{n_no_colocados_3d} caja(s) que la Etapa A asignó por peso/volumen no lograron "
            "ubicarse geométricamente en 3D y fueron descartadas (baja densidad de valor)."
        )

    tab_avion, tab_pallets, tab_no_asignados = st.tabs(
        ["🛫 Vista del avión completo", "📦 Detalle por pallet", "❌ No embarcados"]
    )

    with tab_avion:
        st.plotly_chart(figura_avion(empaques), width='stretch')

    with tab_pallets:
        pos_ids = [e.posicion.id for e in empaques]
        seleccion = st.selectbox("Posición de pallet", pos_ids)
        empaque_sel = next(e for e in empaques if e.posicion.id == seleccion)
        c1, c2 = st.columns([2, 1])
        with c1:
            st.plotly_chart(figura_posicion(empaque_sel), width='stretch')
        with c2:
            peso_pallet = sum(c.paquete.peso_kg for c in empaque_sel.colocadas)
            vol_pallet = sum(c.paquete.volumen_m3 for c in empaque_sel.colocadas)
            st.metric("Peso en pallet", f"{peso_pallet:,.0f} / {empaque_sel.posicion.peso_max_kg:,.0f} kg")
            st.metric(
                "Volumen en pallet",
                f"{vol_pallet:.2f} / {empaque_sel.posicion.volumen_max_m3:.2f} m³",
            )
            st.dataframe(
                pd.DataFrame(
                    [
                        dict(
                            id=c.paquete.id, producto=c.paquete.tipo_producto, peso_kg=c.paquete.peso_kg,
                            apilable=c.paquete.apilable, riesgo_alto=c.paquete.riesgo_alto,
                        )
                        for c in empaque_sel.colocadas
                    ]
                ),
                width='stretch',
                height=250,
            )

    with tab_no_asignados:
        todos_no_embarcados = list(resultado.no_asignados) + [
            c for e in empaques for c in e.no_colocadas
        ]
        if todos_no_embarcados:
            st.dataframe(
                pd.DataFrame(
                    [
                        dict(
                            id=p.id, producto=p.tipo_producto, cliente=p.cliente,
                            peso_kg=p.peso_kg, ingreso_usd=p.ingreso_usd,
                            apilable=p.apilable, riesgo_alto=p.riesgo_alto,
                        )
                        for p in todos_no_embarcados
                    ]
                ),
                width='stretch',
            )
            st.metric(
                "Ingreso no capturado", f"${sum(p.ingreso_usd for p in todos_no_embarcados):,.0f}"
            )
        else:
            st.success("¡Toda la carga disponible fue embarcada!")
else:
    st.info("Genera la carga disponible y presiona **Optimizar carga del avión** para ver resultados.")
