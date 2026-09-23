"""POC — Optimizador de espacio de carga aérea (temporada de flores).

Ejecutar con:  streamlit run app.py
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import pandas as pd
import streamlit as st

from optimizador.datos_simulados import (
    DENSIDAD_KG_M3,
    TARIFA_USD_KG,
    crear_avion,
    generar_catalogo_paquetes,
)
from optimizador.empaquetado_3d import empaquetar_posicion
from optimizador.entidades import Paquete
from optimizador.optimizador_carga import (
    MARGEN_SUPERIOR_META,
    optimizar,
    optimizar_con_meta,
    repartir_obligatorio_en_fila,
)
from optimizador.visualizacion import figura_avion, figura_posicion

_DENSIDAD_PROMEDIO_KG_M3 = sum(DENSIDAD_KG_M3.values()) / len(DENSIDAD_KG_M3)
_TARIFA_PROMEDIO_USD_KG = (
    sum((tarifa_min + tarifa_max) / 2 for tarifa_min, tarifa_max in TARIFA_USD_KG.values())
    / len(TARIFA_USD_KG)
    * 1.05  # prima promedio por destino/cliente (rng.uniform(0.95, 1.15) en datos_simulados.py)
)


def _monto_objetivo_sugerido(avion, factor_seguridad_volumen: float, factor_disponibilidad: float) -> float:
    """Estima el ingreso techo real de este avión con su disponibilidad actual.

    Usa el mismo patrón ya documentado en CLAUDE.md ("el tope de peso por
    posición manda antes que el payload del avión", "el avión se satura en
    volumen, no en peso"): el techo de carga es el mínimo entre el payload
    total, la suma de los topes de peso por pallet, y el peso equivalente
    del volumen disponible a una densidad típica de caja de flores. Es una
    aproximación (el MILP real prioriza ítems de mayor ingreso/kg, así que
    el techo real suele quedar algo por encima de esto) — sirve como punto
    de partida editable, no como el número exacto que dará el optimizador.
    """
    peso_max_pallets = sum(p.peso_max_kg for p in avion.posiciones)
    peso_max_por_volumen = avion.volumen_total_m3 * factor_seguridad_volumen * _DENSIDAD_PROMEDIO_KG_M3
    peso_max_efectivo = factor_disponibilidad * min(
        avion.peso_max_carga_kg, peso_max_pallets, peso_max_por_volumen
    )
    return round(peso_max_efectivo * _TARIFA_PROMEDIO_USD_KG / 500.0) * 500.0


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
    st.header("Stock de temporada")
    n_paquetes = st.slider(
        "Tamaño del stock a simular", 500, 8000, 3000, step=250,
        help="El stock completo disponible para TODA la fila de aviones, no solo este — se va "
        "agotando a medida que cada avión despacha su carga.",
    )
    st.caption(
        "Catálogos grandes (>4000) hacen más lento el MILP (puede tomar 20-35s) — es el precio "
        "de tener un stock realista compartido entre varios aviones."
    )
    seed = st.number_input("Semilla aleatoria", value=42, step=1)
    total_aviones_fila = st.number_input(
        "Aviones planeados en esta fila", min_value=1, value=3, step=1,
        help="Cuántos aviones en total van a despachar este stock. La carga obligatoria no se "
        "fuerza toda en el primer avión que la encuentre — se reparte proporcionalmente entre "
        "los aviones que quedan por optimizar, para que no le toque más de la que puede llevar.",
    )
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
    generar = st.button(
        "🔄 Generar stock nuevo (reinicia la fila)", width='stretch',
        help="Regenera todo el stock desde cero y borra el historial de aviones ya despachados.",
    )

    st.divider()
    n_avion_actual = st.session_state.get("num_avion", 1)
    st.header(f"✈️ Avión #{n_avion_actual} (este)")
    modelo_avion = st.selectbox("Modelo de avión", ["B767F", "B737F", "B777F", "MD11F"], index=0)
    avion = crear_avion(modelo_avion)

    disponibilidad_variable = st.checkbox(
        "Disponibilidad variable por vuelo",
        value=False,
        help="Un avión rara vez vuela con el 100% de su capacidad estructural libre para carga "
        "(derates de combustible/peso — y más adelante, en aviones de pasajeros, espacio "
        "compartido con el equipaje). Si lo activas, este avión sortea su disponibilidad real "
        "dentro del rango de abajo en vez de usar siempre el mismo número — simula la "
        "variabilidad real entre vuelos.",
    )
    if disponibilidad_variable:
        rango_disponibilidad = st.slider(
            "Rango de disponibilidad (%)", 50, 100, (75, 100), step=5,
            help="Este avión sortea un valor dentro de este rango (reproducible: mismo avión + "
            "misma semilla siempre da el mismo sorteo).",
        )
        rng_disponibilidad = random.Random(int(seed) * 1000 + n_avion_actual)
        factor_disponibilidad = rng_disponibilidad.uniform(
            rango_disponibilidad[0] / 100.0, rango_disponibilidad[1] / 100.0
        )
        st.caption(f"🎲 Disponibilidad sorteada para el avión #{n_avion_actual}: {factor_disponibilidad:.0%}")
    else:
        factor_disponibilidad = st.slider(
            "Disponibilidad de este avión (%)", 50, 100, 100, step=5,
            help="Qué % de la capacidad nominal (peso y volumen) está realmente libre para carga "
            "en este vuelo — 100% = capacidad estructural completa.",
        ) / 100.0

    modo_optimizacion = st.radio(
        "¿Qué hace el modelo?",
        ["Cumplir un monto objetivo", "Maximizar ingreso"],
        help=(
            "Cumplir un monto objetivo: el ingreso ya viene decidido para ESTE avión (el área "
            "comercial ya lo optimizó) — el modelo selecciona paquetes cuyo ingreso se acerque a "
            "ese monto sin pasarse por mucho (no es un piso libre: si te dijeron 25.500, no carga "
            "bastante más solo por llenar espacio), y entre esas opciones, usa el espacio "
            "disponible de la forma más eficiente posible. Maximizar ingreso: el modelo elige "
            "libremente la combinación que deja la mayor plata."
        ),
    )
    monto_objetivo = None
    if modo_optimizacion == "Cumplir un monto objetivo":
        sugerido = _monto_objetivo_sugerido(avion, factor_seguridad, factor_disponibilidad)
        clave_sugerido = (modelo_avion, round(factor_disponibilidad, 3), factor_seguridad)
        if st.session_state.get("_clave_monto_sugerido") != clave_sugerido:
            st.session_state["monto_objetivo_input"] = sugerido
            st.session_state["_clave_monto_sugerido"] = clave_sugerido
        monto_objetivo = st.number_input(
            "Monto objetivo de este avión (USD)", min_value=0.0, step=1_000.0,
            key="monto_objetivo_input",
            help="Ingreso a alcanzar con lo que quede en el stock — el modelo no se pasa de esto "
            "por mucho (margen chico, ~2%). Se sugiere automáticamente cerca del techo de "
            f"capacidad de este avión con su disponibilidad actual (≈${sugerido:,.0f}) — "
            "edítalo si el área comercial te dio otro número. Si el stock no da para tanto, se "
            "reporta cuánto falta.",
        )

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
    st.session_state["historial_despachos"] = []
    st.session_state["num_avion"] = 1
    st.session_state.pop("resultado", None)

if st.session_state.get("historial_despachos"):
    with st.expander(f"📋 Historial de despacho ({len(st.session_state['historial_despachos'])} avión(es) ya cargados)", expanded=False):
        st.dataframe(pd.DataFrame(st.session_state["historial_despachos"]), width='stretch')

if n_avion_actual > int(total_aviones_fila):
    df_restante = st.session_state["df_paquetes"]
    st.success(f"✅ Fila completa: los {int(total_aviones_fila)} aviones planeados ya despacharon.")
    kstock1, kstock2 = st.columns(2)
    kstock1.metric("Stock sin embarcar", f"{len(df_restante)} cajas")
    kstock2.metric(
        "Ingreso no capturado",
        f"${df_restante['ingreso_usd'].sum():,.0f}" if len(df_restante) else "$0",
    )
    st.caption(
        "Que quede stock sin embarcar no es un error por sí solo — el stock simulado es el de "
        "toda la temporada, no tiene por qué caber completo en la fila de aviones que planeaste. "
        "Si esperabas que la fila absorbiera todo, compara este remanente contra la capacidad "
        "total de los aviones que usaste."
    )
    st.info(
        "¿Quieres seguir despachando con un avión adicional? Sube **\"Aviones planeados en esta "
        "fila\"** en la barra lateral a un número mayor que el actual y este panel se habilita de "
        "nuevo."
    )
    st.stop()

col_izq, col_der = st.columns([1, 1])
with col_izq:
    st.subheader(f"Stock disponible (avión #{n_avion_actual} toma de aquí)")
    st.caption(
        "Este es el stock que va quedando después de despachar los aviones anteriores — no se "
        "regenera solo. Tabla editable: corrige valores, borra filas (selecciona + tecla Supr) o "
        "agrega cajas nuevas con el ➕ al final de la tabla."
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

    kstock1, kstock2 = st.columns(2)
    kstock1.metric("Stock restante", f"{len(paquetes)} cajas")
    kstock2.metric("Ingreso potencial del stock", f"${sum(p.ingreso_usd for p in paquetes):,.0f}")

    aviones_restantes = max(1, int(total_aviones_fila) - (n_avion_actual - 1))
    capacidad_estimada_fila = aviones_restantes * _monto_objetivo_sugerido(
        avion, factor_seguridad, factor_disponibilidad
    )
    ingreso_restante = sum(p.ingreso_usd for p in paquetes)
    if ingreso_restante > capacidad_estimada_fila * 1.05:
        st.warning(
            f"⚠️ El stock restante (\\${ingreso_restante:,.0f}) supera la capacidad estimada de "
            f"los {aviones_restantes} avión(es) que quedan en la fila (~\\${capacidad_estimada_fila:,.0f}, "
            f"asumiendo que todos son {avion.modelo} con la disponibilidad y factor de seguridad "
            "actuales) — es esperable que quede stock sin embarcar al terminar la fila. Si quieres "
            "que absorba más, sube \"Aviones planeados en esta fila\", usa un avión más grande, o "
            "revisa el tamaño del stock."
        )

    paquetes_efectivos, n_obligatorio_forzado, n_obligatorio_pospuesto = repartir_obligatorio_en_fila(
        paquetes, aviones_restantes,
    )
    if n_obligatorio_pospuesto:
        st.caption(
            f"📦 Carga obligatoria: se fuerzan {n_obligatorio_forzado} cajas en este avión (de "
            f"{n_obligatorio_forzado + n_obligatorio_pospuesto} obligatorias en el stock) — las "
            f"{n_obligatorio_pospuesto} restantes quedan pendientes para los "
            f"{aviones_restantes - 1} avión(es) que quedan en la fila (avión #{n_avion_actual} "
            f"de {total_aviones_fila} planeados)."
        )

with col_der:
    st.subheader(f"Avión #{n_avion_actual}: {avion.modelo}")
    st.write(
        f"**Posiciones de pallet:** {len(avion.posiciones)} "
        f"({len({p.estacion for p in avion.posiciones})} estaciones"
        f"{', izquierdo/derecho' if any(p.lado != 'centro' for p in avion.posiciones) else ''})  \n"
        f"**Payload máximo:** {avion.peso_max_carga_kg:,.0f} kg  \n"
        f"**Volumen total:** {avion.volumen_total_m3:,.1f} m³ (ya descuenta el contorno del fuselaje)  \n"
        f"**Rango CG admisible:** {avion.cg_min_m} – {avion.cg_max_m} m"
    )
    if factor_disponibilidad < 1.0:
        st.write(
            f"**Disponible este vuelo ({factor_disponibilidad:.0%}):** "
            f"{factor_disponibilidad * avion.peso_max_carga_kg:,.0f} kg, "
            f"{factor_disponibilidad * avion.volumen_total_m3:,.1f} m³"
        )
    st.caption(
        "Cada pallet tiene su contorno recortado hacia el fuselaje (menos altura útil junto "
        "a la pared) y respeta que nada se apoye sobre carga no apilable o de alto riesgo (⚠)."
    )

st.divider()

if st.button(f"🚀 Optimizar carga del avión #{n_avion_actual}", type="primary"):
    with st.spinner("Resolviendo modelo de asignación (MILP)..."):
        if modo_optimizacion == "Cumplir un monto objetivo":
            resultado = optimizar_con_meta(
                paquetes_efectivos, avion, monto_objetivo_usd=monto_objetivo, factor_seguridad_volumen=factor_seguridad,
                factor_disponibilidad=factor_disponibilidad,
            )
        else:
            resultado = optimizar(
                paquetes_efectivos, avion, factor_seguridad_volumen=factor_seguridad,
                factor_disponibilidad=factor_disponibilidad,
            )

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
    vol_total = sum(c.paquete.volumen_m3 for e in empaques for c in e.colocadas)

    es_modo_meta = resultado.monto_objetivo_usd is not None

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Ingreso total", f"${resultado.ingreso_total:,.0f}")
    if es_modo_meta:
        k2.metric("Meta objetivo", f"${resultado.monto_objetivo_usd:,.0f}")
        k3.metric(
            "Cumple meta" if resultado.cumple_meta else "Falta para la meta",
            "✅ Sí" if resultado.cumple_meta else f"${resultado.faltante_para_meta_usd:,.0f}",
        )
    else:
        k2.metric(
            "Utilización de ingreso",
            f"{100 * resultado.ingreso_total / resultado.ingreso_potencial:.1f}%"
            if resultado.ingreso_potencial else "—",
        )
        k3.metric("Cajas cargadas", f"{n_colocados_3d} / {len(paquetes)}")
    k4.metric("Utilización de volumen", f"{100 * vol_total / avion.volumen_total_m3:.1f}%")
    k5.metric("Peso total cargado", f"{peso_total:,.0f} kg")
    k6.metric(
        "CG resultante",
        f"{resultado.brazo_resultante_m:.2f} m" if resultado.brazo_resultante_m else "—",
    )

    if es_modo_meta:
        st.caption(f"Cajas cargadas: {n_colocados_3d} / {len(paquetes)} — objetivo: acercarse a la meta sin pasarse, y entre eso, usar bien el volumen y peso disponibles.")
        if not resultado.cumple_meta:
            st.warning(
                f"No se alcanzó la meta de \\${resultado.monto_objetivo_usd:,.0f}: con el catálogo y "
                f"espacio disponibles, el máximo posible es \\${resultado.ingreso_maximo_posible:,.0f} "
                f"(faltan \\${resultado.faltante_para_meta_usd:,.0f}). Agrega más cajas o revisa el catálogo."
            )

    with st.expander(
        "🔍 ¿Por qué este resultado? (diagnóstico)",
        expanded=es_modo_meta and not resultado.cumple_meta,
    ):
        ingreso_cargado_real = sum(c.paquete.ingreso_usd for e in empaques for c in e.colocadas)
        if abs(ingreso_cargado_real - resultado.ingreso_total) > 1:
            st.warning(
                f"La Etapa A seleccionó \\${resultado.ingreso_total:,.0f} por peso/volumen agregado, "
                f"pero {n_no_colocados_3d} caja(s) de esas no lograron ubicarse en el empaquetado 3D "
                f"real (no había cómo acomodarlas geométricamente) y quedaron en tierra. Lo que "
                f"realmente termina cargado en el avión es \\${ingreso_cargado_real:,.0f}."
            )

        vol_permitido_m3 = factor_seguridad * avion.volumen_total_m3
        vol_pct_permitido = vol_total / vol_permitido_m3 if vol_permitido_m3 else 0.0
        peso_pct = peso_total / avion.peso_max_carga_kg if avion.peso_max_carga_kg else 0.0

        st.markdown("**¿Qué se llenó primero: el volumen o el peso?**")
        c1, c2 = st.columns(2)
        with c1:
            st.caption(
                f"Volumen: {vol_total:.1f} m³ usados de {vol_permitido_m3:.1f} m³ permitidos "
                f"(factor de seguridad {factor_seguridad:.0%} de los {avion.volumen_total_m3:.1f} m³ "
                "físicos totales)"
            )
            st.progress(min(vol_pct_permitido, 1.0))
        with c2:
            st.caption(
                f"Peso: {peso_total:,.0f} kg usados de {avion.peso_max_carga_kg:,.0f} kg de payload "
                "máximo"
            )
            st.progress(min(peso_pct, 1.0))

        # El MILP asigna por POSICIÓN de pallet (cada una con su propio tope de peso/volumen), no
        # contra un solo pozo agregado — así que un avión puede verse con margen "en total" y aun
        # así tener varios pallets individuales ya llenos, que es lo que de verdad frena que entren
        # más cajas (el resto simplemente no cabe AHÍ, aunque sobre espacio en otro pallet).
        posiciones_llenas_vol = 0
        posiciones_llenas_peso = 0
        for pos in avion.posiciones:
            asignados_pos = resultado.asignacion[pos.id]
            vol_max_pos = factor_seguridad * pos.volumen_max_m3
            peso_pos = sum(p.peso_kg for p in asignados_pos)
            vol_pos = sum(p.volumen_m3 for p in asignados_pos)
            if vol_max_pos and vol_pos / vol_max_pos >= 0.95:
                posiciones_llenas_vol += 1
            if pos.peso_max_kg and peso_pos / pos.peso_max_kg >= 0.95:
                posiciones_llenas_peso += 1
        n_posiciones = len(avion.posiciones)

        if posiciones_llenas_vol or posiciones_llenas_peso:
            partes = []
            if posiciones_llenas_vol:
                partes.append(f"{posiciones_llenas_vol}/{n_posiciones} al tope de **volumen**")
            if posiciones_llenas_peso:
                partes.append(f"{posiciones_llenas_peso}/{n_posiciones} al tope de **peso**")
            nota_empaquetado = (
                " (el volumen *realmente cargado* de arriba queda por debajo de este tope "
                "precisamente porque el empaquetado 3D real no logró ubicar todo lo que la Etapa A "
                "asignó — ver aviso arriba; no es que sobrara capacidad sin usar)."
                if n_no_colocados_3d
                else "."
            )
            st.info(
                f"La Etapa A ya dejó {' y '.join(partes)} — el modelo asigna posición por "
                "posición (no contra un solo pozo agregado), así que el resto de las cajas no cabía "
                f"ahí aunque sobrara espacio en otros pallets. Es el cuello de botella real" + nota_empaquetado
                + " Subir el factor de seguridad de volumen (si el empaquetado 3D real lo permite) o "
                "usar un avión más grande ayudaría a cargar más."
            )
        elif vol_pct_permitido >= 0.9 > peso_pct:
            st.info(
                "El **volumen** agregado es el cuello de botella: el avión se llena de espacio "
                "mucho antes que de peso — típico en carga de flores (baja densidad, poco peso por "
                "m³). Subir el factor de seguridad de volumen, usar un avión más grande, o reducir "
                "el % de carga no apilable del stock ayudaría a cargar más ingreso."
            )
        elif peso_pct >= 0.9 > vol_pct_permitido:
            st.info(
                "El **peso** agregado es el cuello de botella: se llegó al payload máximo del avión "
                "con volumen de sobra. Poco común con flores, pero puede pasar si el stock tiene "
                "mucha carga densa (agua, contenedores, etc.)."
            )
        elif vol_pct_permitido >= 0.9 and peso_pct >= 0.9:
            st.info("Volumen y peso están al límite a la vez: el avión está prácticamente lleno.")
        else:
            st.info(
                "Ni el volumen ni el peso (ni en total ni en ningún pallet individual) están al "
                "límite — el modelo se detuvo antes por el balance (centro de gravedad) o porque no "
                "quedan más cajas en el stock que convenga agregar (obligatorias aparte, todas las "
                "que agregan valor ya están)."
            )

        if es_modo_meta and not resultado.cumple_meta:
            diferencia_por_empaquetado = resultado.ingreso_maximo_posible - resultado.ingreso_total
            st.markdown(
                f"**¿Sumar otras cajas y sacar otras daría más plata?** No: el ingreso máximo que "
                f"matemáticamente se puede lograr con este stock y la capacidad de este avión (peso, "
                f"volumen y balance) es \\${resultado.ingreso_maximo_posible:,.0f} — ninguna otra "
                "combinación de paquetes puede superar ese techo, la Etapa A ya lo prueba al "
                "resolverlo. "
                + (
                    f"Lo cargado aquí (\\${resultado.ingreso_total:,.0f}) es algo menos que ese techo "
                    "porque se sacrificaron "
                    f"\\${diferencia_por_empaquetado:,.0f} de ingreso a cambio de aprovechar mejor el "
                    "espacio disponible. "
                    if diferencia_por_empaquetado > 1
                    else ""
                )
                + f"Para cerrar los \\${resultado.faltante_para_meta_usd:,.0f} que faltan hace falta "
                "más capacidad (avión más grande o más factor de seguridad de volumen) o más cajas "
                "de alto valor en el stock — no una mejor selección de las mismas cajas."
            )

        no_seleccionados = sorted(resultado.no_asignados, key=lambda p: -p.densidad_valor)[:8]
        if no_seleccionados:
            st.markdown(
                "**Paquetes de mayor valor por m³ que quedaron fuera** — los que más convendría "
                "meter si hubiera más espacio o peso disponible (y por qué el modelo los descartó: "
                "compara su volumen/peso contra lo que queda libre arriba):"
            )
            st.dataframe(
                pd.DataFrame(
                    [
                        dict(
                            id=p.id, producto=p.tipo_producto, ingreso_usd=p.ingreso_usd,
                            volumen_m3=round(p.volumen_m3, 3), ingreso_por_m3=round(p.densidad_valor, 0),
                            peso_kg=p.peso_kg,
                        )
                        for p in no_seleccionados
                    ]
                ),
                width='stretch', height=250,
            )

    if n_no_colocados_3d:
        st.info(
            f"{n_no_colocados_3d} caja(s) que la Etapa A asignó por peso/volumen no lograron "
            "ubicarse geométricamente en 3D y fueron descartadas (baja densidad de valor)."
        )

    st.caption(
        f"Esto es una previsualización del avión #{n_avion_actual} — el stock recién se descuenta "
        "cuando confirmas el despacho."
    )
    if st.button(f"✅ Confirmar despacho del avión #{n_avion_actual} y pasar al siguiente", type="primary"):
        ids_consumidos = {c.paquete.id for e in empaques for c in e.colocadas}
        st.session_state["df_paquetes"] = (
            df_editado[~df_editado["id"].isin(ids_consumidos)].reset_index(drop=True)
        )
        st.session_state.setdefault("historial_despachos", []).append(
            dict(
                avion=f"Avión #{n_avion_actual}", modelo=avion.modelo,
                monto_objetivo=resultado.monto_objetivo_usd,
                ingreso_logrado=round(resultado.ingreso_total, 0),
                cajas=n_colocados_3d,
                utilizacion_volumen_pct=round(100 * vol_total / avion.volumen_total_m3, 1),
            )
        )
        st.session_state["num_avion"] = n_avion_actual + 1
        st.session_state.pop("resultado", None)
        st.session_state.pop("empaques", None)
        st.rerun()

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
        ids_no_colocados_etapa_b = {c.id for e in empaques for c in e.no_colocadas}
        todos_no_embarcados = list(resultado.no_asignados) + [
            c for e in empaques for c in e.no_colocadas
        ]
        if todos_no_embarcados:
            # Peso/volumen libre real por posición, con lo que efectivamente quedó
            # cargado (post Etapa B) — mismo criterio de capacidad efectiva que usa
            # el panel de diagnóstico (factor_disponibilidad * factor_seguridad).
            peso_libre_pos = {}
            vol_libre_pos = {}
            for e in empaques:
                peso_usado = sum(c.paquete.peso_kg for c in e.colocadas)
                vol_usado = sum(c.paquete.volumen_m3 for c in e.colocadas)
                peso_libre_pos[e.posicion.id] = factor_disponibilidad * e.posicion.peso_max_kg - peso_usado
                vol_libre_pos[e.posicion.id] = (
                    factor_disponibilidad * factor_seguridad * e.posicion.volumen_max_m3 - vol_usado
                )
            peso_libre_avion = factor_disponibilidad * avion.peso_max_carga_kg - peso_total

            techo_meta = (
                resultado.monto_objetivo_usd * (1 + MARGEN_SUPERIOR_META)
                if es_modo_meta and resultado.monto_objetivo_usd
                else None
            )

            filas_no_embarcados = []
            for p in todos_no_embarcados:
                cabe_en_pallet = any(
                    p.peso_kg <= peso_libre_pos[pos_id] + 1e-6 and p.volumen_m3 <= vol_libre_pos[pos_id] + 1e-6
                    for pos_id in peso_libre_pos
                )
                cabe_en_avion = p.peso_kg <= peso_libre_avion + 1e-6
                excede_techo = (
                    techo_meta is not None
                    and (resultado.ingreso_total + p.ingreso_usd) > techo_meta + 1e-6
                )

                if p.id in ids_no_colocados_etapa_b:
                    cabria = False
                    razon = (
                        "La Etapa A lo seleccionó, pero el empaquetado 3D no encontró dónde "
                        "ubicarlo geométricamente (forma, apilamiento o contorno del pallet)."
                    )
                elif not (cabe_en_pallet and cabe_en_avion):
                    cabria = False
                    razon = "No queda peso/volumen libre (en ningún pallet, o en el payload total del avión) para agregarlo sin sacar otra caja."
                elif excede_techo:
                    cabria = True
                    razon = (
                        f"Cabría físicamente, pero sumar sus ${p.ingreso_usd:,.0f} pasaría el techo "
                        f"de la meta (vas en ${resultado.ingreso_total:,.0f} de un techo de "
                        f"${techo_meta:,.0f})."
                    )
                else:
                    cabria = True
                    razon = (
                        "Cabría en peso/volumen (por pallet y en total) sin sacar nada — el balance "
                        "(CG) combinado con el resto de la carga puede no permitirlo, o el solver no "
                        "llegó a la solución exactamente óptima (ver estado del solver arriba)."
                    )

                filas_no_embarcados.append(
                    dict(
                        id=p.id, producto=p.tipo_producto, cliente=p.cliente,
                        peso_kg=p.peso_kg, ingreso_usd=p.ingreso_usd,
                        apilable=p.apilable, riesgo_alto=p.riesgo_alto,
                        cabria_sin_sacar_nada="✅" if cabria else "❌",
                        por_que_no=razon,
                    )
                )

            st.caption(
                "\"¿Cabría sin sacar nada?\": chequeo agregado de peso/volumen libre por pallet y en "
                "el avión — no garantiza que la geometría 3D real lo acomode (mismo nivel de "
                "aproximación que el resto del diagnóstico), y no revisa el balance (CG)."
            )
            st.dataframe(pd.DataFrame(filas_no_embarcados), width='stretch')

            n_cabrian = sum(1 for f in filas_no_embarcados if f["cabria_sin_sacar_nada"] == "✅")
            ingreso_cabria = sum(
                p.ingreso_usd for p, f in zip(todos_no_embarcados, filas_no_embarcados)
                if f["cabria_sin_sacar_nada"] == "✅"
            )
            k1, k2 = st.columns(2)
            k1.metric("Ingreso no capturado", f"${sum(p.ingreso_usd for p in todos_no_embarcados):,.0f}")
            k2.metric(f"De eso, cabría sin sacar nada ({n_cabrian} cajas)", f"${ingreso_cabria:,.0f}")
        else:
            st.success("¡Toda la carga disponible fue embarcada!")
else:
    st.info(f"Ajusta el monto objetivo del avión #{n_avion_actual} y presiona **Optimizar carga del avión** para ver resultados.")
