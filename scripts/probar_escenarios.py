"""Stress-test del motor de optimización (Etapa A + Etapa B) contra varios
escenarios comunes y de borde, sin pasar por Streamlit/Playwright.

Llama directo a las mismas funciones que usa `app.py` (`optimizar`,
`optimizar_con_meta`, `empaquetar_posicion`), así corre en segundos y sirve
como regresión rápida antes de tocar el MILP, el empaquetado 3D o el
panel de diagnóstico. No reemplaza una pasada visual por la UI real, pero
cubre la parte donde de verdad viven los bugs (selección, geometría,
balance) mucho más rápido y de forma determinística.

Uso:
    python3 scripts/probar_escenarios.py
    python3 scripts/probar_escenarios.py --solo "meta pegada al techo"
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from optimizador.datos_simulados import crear_avion, generar_catalogo_paquetes  # noqa: E402
from optimizador.empaquetado_3d import empaquetar_posicion  # noqa: E402
from optimizador.optimizador_carga import (  # noqa: E402
    optimizar,
    optimizar_con_meta,
    repartir_obligatorio_en_fila,
)

# Tamaños de fila a probar para el reparto de carga obligatoria — pedidos
# explícitamente para cubrir filas chicas y grandes, no solo un caso.
AVIONES_EN_FILA_A_PROBAR = [3, 5, 7, 10]

# Casos comunes y de borde. Cubren: meta fácil/inalcanzable/pegada al techo,
# modo maximizar ingreso, ambos modelos de avión, stock diminuto, extremos de
# % no apilable/riesgo (incluido el control en 0%), meta=0, todo obligatorio,
# factor de seguridad de volumen bajo, carga densa (peso como cuello de
# botella real — el generador de flores nunca lo produce solo, hay que
# forzarlo) y un catálogo más allá del tope actual de la UI (8000).
ESCENARIOS = [
    dict(nombre="meta fácil de cumplir", modelo="B767F", n=1500, seed=1,
         monto=10_000, pct_obl=0.10, pct_no_apil=0.15, pct_riesgo=0.06, factor_seg=0.85),
    dict(nombre="meta inalcanzable (muy por sobre el techo)", modelo="B767F", n=1500, seed=1,
         monto=100_000, pct_obl=0.10, pct_no_apil=0.15, pct_riesgo=0.06, factor_seg=0.85),
    dict(nombre="meta pegada al techo (shortcut de Fase 2)", modelo="B767F", n=3000, seed=42,
         monto=27_700, pct_obl=0.10, pct_no_apil=0.15, pct_riesgo=0.06, factor_seg=0.85),
    dict(nombre="maximizar ingreso (sin meta)", modelo="B767F", n=2000, seed=7,
         monto=None, pct_obl=0.10, pct_no_apil=0.15, pct_riesgo=0.06, factor_seg=0.85),
    dict(nombre="B737F chico", modelo="B737F", n=500, seed=3,
         monto=8_000, pct_obl=0.10, pct_no_apil=0.15, pct_riesgo=0.06, factor_seg=0.85),
    dict(nombre="stock diminuto frente a la meta", modelo="B737F", n=50, seed=5,
         monto=50_000, pct_obl=0.10, pct_no_apil=0.15, pct_riesgo=0.06, factor_seg=0.85),
    dict(nombre="alto % no apilable/riesgo (borde)", modelo="B767F", n=2000, seed=9,
         monto=20_000, pct_obl=0.10, pct_no_apil=0.50, pct_riesgo=0.30, factor_seg=0.85),
    dict(nombre="0% no apilable (control, debería dar el mejor % de volumen)", modelo="B767F", n=2000, seed=9,
         monto=20_000, pct_obl=0.0, pct_no_apil=0.0, pct_riesgo=0.0, factor_seg=0.85),
    dict(nombre="meta = 0", modelo="B767F", n=1000, seed=2,
         monto=0, pct_obl=0.10, pct_no_apil=0.15, pct_riesgo=0.06, factor_seg=0.85),
    dict(nombre="100% obligatorio (contrato)", modelo="B767F", n=300, seed=11,
         monto=5_000, pct_obl=1.0, pct_no_apil=0.15, pct_riesgo=0.06, factor_seg=0.85),
    dict(nombre="factor de seguridad de volumen bajo", modelo="B767F", n=2000, seed=9,
         monto=20_000, pct_obl=0.10, pct_no_apil=0.15, pct_riesgo=0.06, factor_seg=0.50),
    dict(nombre="catálogo grande (8000, tope del slider)", modelo="B767F", n=8000, seed=42,
         monto=35_000, pct_obl=0.10, pct_no_apil=0.15, pct_riesgo=0.06, factor_seg=0.85),
    dict(nombre="carga muy densa (fuerza que el peso sea el cuello de botella)", modelo="B767F", n=2000, seed=13,
         monto=25_000, pct_obl=0.10, pct_no_apil=0.15, pct_riesgo=0.06, factor_seg=0.85, denso=True),
    dict(nombre="carga muy densa en B737F (payload mucho más chico, 10000 kg)", modelo="B737F", n=800, seed=13,
         monto=12_000, pct_obl=0.10, pct_no_apil=0.15, pct_riesgo=0.06, factor_seg=0.85, denso=True),
    dict(nombre="catálogo 10000 (más allá del tope actual de la UI)", modelo="B767F", n=10_000, seed=42,
         monto=35_000, pct_obl=0.10, pct_no_apil=0.15, pct_riesgo=0.06, factor_seg=0.85),
    # % obligatorio alto (20%, dentro del rango 0-40% que permite el slider de
    # la UI) con un stock grande (8000, el tope de la UI): la carga obligatoria
    # POR SÍ SOLA ya no cabe (necesita ~93.7 m³, hay 55.8 m³ permitidos) — el
    # MILP da "Infeasible" de verdad, no un timeout. El reparo de capacidad lo
    # rescata igual (nunca revienta ni viola capacidad), pero de las ~1580
    # cajas obligatorias solo caben ~650 — el resto se descarta en silencio
    # pese a estar marcadas "debe ir sí o sí". Ver CLAUDE.md.
    dict(nombre="obligatorio excede la capacidad del avión (20%, dentro del rango de la UI)", modelo="B767F", n=8000, seed=42,
         monto=25_000, pct_obl=0.20, pct_no_apil=0.15, pct_riesgo=0.06, factor_seg=0.85,
         estados_ok=("Optimal", "Not Solved", "Infeasible")),
]

# Peso por caja para los escenarios "denso": muy por sobre el rango real de
# flores (~2-15 kg por caja) para que el payload del avión (52.000 kg en el
# B767F, 10.000 kg en el B737F) se agote mucho antes que el volumen — el
# generador de flores reales nunca produce esto por sí solo. Un primer
# intento con 15-25 kg NO alcanzó a forzar esto (~920 cajas caben por
# volumen en el B767F, y a 20 kg promedio eso es solo ~18.400 kg, muy por
# debajo del payload — el volumen seguía ganando). Con ~920 cajas cabiendo
# por volumen, hace falta más de 52.000/920 ≈ 56.5 kg/caja para que el peso
# gane la carrera — 65-95 kg da margen de sobra.
PESO_DENSO_KG_MIN = 65.0
PESO_DENSO_KG_MAX = 95.0


def _densificar(paquetes: list, seed: int) -> list:
    rng = random.Random(seed + 999)
    return [replace(p, peso_kg=round(rng.uniform(PESO_DENSO_KG_MIN, PESO_DENSO_KG_MAX), 2)) for p in paquetes]


def _contar_violaciones_apilado(empaques) -> int:
    """Auditoría independiente: recorre las cajas ya colocadas y verifica que
    nada quede apoyado sobre algo no apilable. Deliberadamente NO reutiliza
    `_genera_apoyo_invalido` de empaquetado_3d.py — si ese código tiene un
    bug, un chequeo que comparte la misma lógica no lo va a detectar.
    """
    violaciones = 0
    for empaque in empaques:
        cajas = empaque.colocadas
        for i, a in enumerate(cajas):
            for b in cajas[i + 1:]:
                solapa_xy = not (
                    a.x_cm + a.largo_cm <= b.x_cm + 1e-6 or b.x_cm + b.largo_cm <= a.x_cm + 1e-6
                    or a.y_cm + a.ancho_cm <= b.y_cm + 1e-6 or b.y_cm + b.ancho_cm <= a.y_cm + 1e-6
                )
                if not solapa_xy:
                    continue
                if abs((a.z_cm + a.alto_cm) - b.z_cm) < 0.05 and not a.paquete.permite_apilado_encima:
                    violaciones += 1
                if abs((b.z_cm + b.alto_cm) - a.z_cm) < 0.05 and not b.paquete.permite_apilado_encima:
                    violaciones += 1
    return violaciones


def _validar_capacidades(resultado, avion, factor_seguridad: float) -> list[str]:
    """Chequeo de sanidad de la Etapa A: nada debería exceder peso/volumen
    por posición, payload del avión, o el rango de CG. Si esto falla, el MILP
    o su extracción de resultado tiene un bug real."""
    problemas = []
    for pos in avion.posiciones:
        asignados = resultado.asignacion[pos.id]
        peso = sum(p.peso_kg for p in asignados)
        vol = sum(p.volumen_m3 for p in asignados)
        if peso > pos.peso_max_kg + 1e-6:
            problemas.append(f"{pos.id}: peso {peso:.1f} > máx {pos.peso_max_kg}")
        if vol > factor_seguridad * pos.volumen_max_m3 + 1e-6:
            problemas.append(f"{pos.id}: volumen {vol:.2f} > máx {factor_seguridad * pos.volumen_max_m3:.2f}")
    peso_total = sum(p.peso_kg for lst in resultado.asignacion.values() for p in lst)
    if peso_total > avion.peso_max_carga_kg + 1e-6:
        problemas.append(f"payload total {peso_total:.0f} > máx {avion.peso_max_carga_kg}")
    if resultado.brazo_resultante_m is not None and not (
        avion.cg_min_m - 1e-6 <= resultado.brazo_resultante_m <= avion.cg_max_m + 1e-6
    ):
        problemas.append(f"CG {resultado.brazo_resultante_m:.2f} fuera de [{avion.cg_min_m}, {avion.cg_max_m}]")
    return problemas


def verificar_reparto_obligatorio() -> bool:
    """Valida `repartir_obligatorio_en_fila()`: con un stock donde la carga
    obligatoria por sí sola NO cabe en un solo avión (n=8000, 20%
    obligatorio — el mismo caso de borde ya confirmado alcanzable desde la
    UI), prueba filas de distinto largo (3, 5, 7, 10 aviones) y confirma que:
      - forzados + pospuestos == total de obligatorias del stock (el reparto
        no pierde ni duplica cajas en la contabilidad),
      - el resultado final (Etapa A + Etapa B) sigue sin violar capacidad ni
        apilamiento en ningún caso, sea cual sea el largo de la fila.
    """
    print("=== Reparto de carga obligatoria entre aviones de la fila (n=8000, 20% obligatorio) ===\n")
    paquetes = generar_catalogo_paquetes(n=8000, seed=42, pct_obligatorio=0.20, pct_no_apilable=0.15, pct_riesgo_alto=0.06)
    avion = crear_avion("B767F")
    total_obligatorio = sum(1 for p in paquetes if p.obligatorio)
    ok = True
    for aviones_restantes in AVIONES_EN_FILA_A_PROBAR:
        efectivos, forzados, pospuestos = repartir_obligatorio_en_fila(paquetes, aviones_restantes)
        if forzados + pospuestos != total_obligatorio:
            print(
                f"⚠️  aviones_restantes={aviones_restantes}: forzados+pospuestos "
                f"({forzados + pospuestos}) != total obligatorio ({total_obligatorio})"
            )
            ok = False

        resultado = optimizar_con_meta(efectivos, avion, monto_objetivo_usd=25_000, factor_seguridad_volumen=0.85)
        empaques = [empaquetar_posicion(pos, resultado.asignacion[pos.id]) for pos in avion.posiciones]
        problemas = _validar_capacidades(resultado, avion, 0.85)
        violaciones = _contar_violaciones_apilado(empaques)
        estado_fila_ok = not problemas and not violaciones
        if not estado_fila_ok:
            ok = False
        marca = "✅" if estado_fila_ok else "❌"
        print(
            f"aviones_restantes={aviones_restantes:<2} forzados={forzados:<4} pospuestos={pospuestos:<4} "
            f"estado_solver={resultado.estado_solver:<10} {marca}"
        )
        if problemas:
            print(f"    problemas de capacidad: {problemas}")
        if violaciones:
            print(f"    {violaciones} violación(es) de apilamiento")
    print()
    return ok


def correr(escenario: dict) -> dict:
    paquetes = generar_catalogo_paquetes(
        n=escenario["n"], seed=escenario["seed"], pct_obligatorio=escenario["pct_obl"],
        pct_no_apilable=escenario["pct_no_apil"], pct_riesgo_alto=escenario["pct_riesgo"],
    )
    if escenario.get("denso"):
        paquetes = _densificar(paquetes, escenario["seed"])
    avion = crear_avion(escenario["modelo"])

    t0 = time.time()
    if escenario["monto"] is not None:
        resultado = optimizar_con_meta(
            paquetes, avion, monto_objetivo_usd=escenario["monto"],
            factor_seguridad_volumen=escenario["factor_seg"],
        )
    else:
        resultado = optimizar(paquetes, avion, factor_seguridad_volumen=escenario["factor_seg"])
    t_milp = time.time() - t0

    empaques = [empaquetar_posicion(pos, resultado.asignacion[pos.id]) for pos in avion.posiciones]
    t_total = time.time() - t0

    ingreso_real = sum(c.paquete.ingreso_usd for e in empaques for c in e.colocadas)
    vol_total = sum(c.paquete.volumen_m3 for e in empaques for c in e.colocadas)
    peso_total = sum(c.paquete.peso_kg for e in empaques for c in e.colocadas)
    n_colocados = sum(len(e.colocadas) for e in empaques)
    n_no_colocados_3d = sum(len(e.no_colocadas) for e in empaques)

    problemas = _validar_capacidades(resultado, avion, escenario["factor_seg"])
    violaciones = _contar_violaciones_apilado(empaques)

    return dict(
        nombre=escenario["nombre"],
        estado_solver=resultado.estado_solver,
        ingreso_etapa_a=round(resultado.ingreso_total),
        ingreso_real=round(ingreso_real),
        meta=resultado.monto_objetivo_usd,
        cumple_meta=resultado.cumple_meta if resultado.monto_objetivo_usd is not None else None,
        util_vol_pct=round(100 * vol_total / avion.volumen_total_m3, 1) if avion.volumen_total_m3 else 0.0,
        peso_kg=round(peso_total),
        payload_max=avion.peso_max_carga_kg,
        n_paquetes_stock=len(paquetes),
        n_colocados=n_colocados,
        n_no_colocados_3d=n_no_colocados_3d,
        violaciones_apilado=violaciones,
        problemas_capacidad=problemas,
        t_milp_s=round(t_milp, 1),
        t_total_s=round(t_total, 1),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solo", help="Corre solo el escenario cuyo nombre contiene este texto")
    parser.add_argument(
        "--sin-reparto", action="store_true",
        help="Salta la verificación de repartir_obligatorio_en_fila() (más rápido si ya confías en eso)",
    )
    args = parser.parse_args()

    escenarios = ESCENARIOS
    if args.solo:
        escenarios = [e for e in ESCENARIOS if args.solo.lower() in e["nombre"].lower()]
        if not escenarios:
            print(f"Ningún escenario coincide con --solo {args.solo!r}")
            return 1

    hay_problemas = False
    if not args.solo and not args.sin_reparto:
        if not verificar_reparto_obligatorio():
            hay_problemas = True

    filas = []
    for escenario in escenarios:
        print(f"→ {escenario['nombre']} ...", flush=True)
        fila = correr(escenario)
        filas.append(fila)
        estados_ok = escenario.get("estados_ok", ("Optimal", "Not Solved"))
        if fila["violaciones_apilado"] or fila["problemas_capacidad"] or fila["estado_solver"] not in estados_ok:
            hay_problemas = True

    ancho_nombre = max(len(f["nombre"]) for f in filas)
    encabezado = (
        f"{'Escenario':<{ancho_nombre}}  {'Solver':<10}  {'Ing.EtapaA':>10}  {'Ing.real':>10}  "
        f"{'Meta':>10}  {'Cumple':>7}  {'Vol%':>6}  {'Peso/Max':>14}  {'Coloc':>6}  {'NoColoc3D':>9}  "
        f"{'ViolApil':>8}  {'t(s)':>6}"
    )
    print()
    print(encabezado)
    print("-" * len(encabezado))
    for f in filas:
        meta_str = f"{f['meta']:,.0f}" if f["meta"] is not None else "—"
        cumple_str = "✅" if f["cumple_meta"] else ("❌" if f["cumple_meta"] is False else "—")
        peso_str = f"{f['peso_kg']:,}/{f['payload_max']:,.0f}"
        print(
            f"{f['nombre']:<{ancho_nombre}}  {f['estado_solver']:<10}  {f['ingreso_etapa_a']:>10,}  "
            f"{f['ingreso_real']:>10,}  {meta_str:>10}  {cumple_str:>7}  {f['util_vol_pct']:>6}  "
            f"{peso_str:>14}  {f['n_colocados']:>6}  {f['n_no_colocados_3d']:>9}  "
            f"{f['violaciones_apilado']:>8}  {f['t_total_s']:>6}"
        )

    print()
    for escenario, f in zip(escenarios, filas):
        estados_ok = escenario.get("estados_ok", ("Optimal", "Not Solved"))
        if f["problemas_capacidad"]:
            hay_problemas = True
            print(f"⚠️  {f['nombre']}: problemas de capacidad → {f['problemas_capacidad']}")
        if f["violaciones_apilado"]:
            print(f"⚠️  {f['nombre']}: {f['violaciones_apilado']} violación(es) de apilamiento en el 3D")
        if f["estado_solver"] not in estados_ok:
            print(f"⚠️  {f['nombre']}: estado del solver inesperado → {f['estado_solver']}")

    if hay_problemas:
        print("\n❌ Se encontraron problemas — revisar arriba.")
        return 1
    print("\n✅ Todos los escenarios pasaron las validaciones (capacidad, apilamiento, estado del solver).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
