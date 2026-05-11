# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import math
import struct
import wave

import streamlit as st
import pandas as pd
from pyproj import Geod

from core.auth_service import require_authentication
from core.cache_service import ensure_api_cache_cleaned_for_session
from core.config import APP_TITLE
from core.data_service import load_farms
from core.time_context import now_local
from ui.header import render_top_header
from ui.map_view import build_main_map
from ui.sidebar import (
    maybe_auto_refresh_analysis,
    render_sidebar,
)
from ui.styles import apply_styles
from ui.weather_tabs import render_climate_trend_tab, render_weather_forecast_tab
from services.gee_service import gee_diagnostics, load_gee_catalog


st.set_page_config(page_title=APP_TITLE, page_icon=":fire:", layout="wide")

GEOD = Geod(ellps="WGS84")


def render_technical_log(selected_companies) -> None:
    st.subheader("Log Técnico")
    cols = st.columns(2)
    with cols[0]:
        st.metric("Empresa" if len(selected_companies) == 1 else "Empresas", len(selected_companies))
    with cols[1]:
        st.metric("Camadas GE", len(st.session_state.get("gee_applied_indicators", [])))

    stage_cols = st.columns(2)
    with stage_cols[0]:
        st.markdown("#### 1. Configurar")
        st.caption("Confirme a empresa do projeto e aplique camadas GE no menu lateral.")
        if selected_companies:
            status = "Projeto com empresa ativa." if len(selected_companies) == 1 else "Projeto com empresas selecionadas."
            st.success(status)
        else:
            st.info("Nenhuma empresa selecionada.")
    with stage_cols[1]:
        st.markdown("#### 2. Monitorar")
        st.caption("Use o mapa operacional para visualizar perimetros, risco e hotspots.")
        if st.session_state.get("fire_detection_summary") or st.session_state.get("fire_risk_layers"):
            st.success("Dados de risco/GE aplicados.")
        else:
            st.info("Camadas GE ainda nao aplicadas.")

    if selected_companies:
        st.markdown("#### Empresa ativa" if len(selected_companies) == 1 else "#### Empresas ativas")
        st.dataframe({"Empresa": selected_companies}, use_container_width=True, hide_index=True)
    if st.session_state.get("fire_risk_status"):
        st.info(st.session_state["fire_risk_status"])
    if st.session_state.get("last_goes_time"):
        st.caption(f"Ultima imagem GOES: {st.session_state['last_goes_time']}")
    if st.session_state.get("roi_limit_status"):
        st.caption(st.session_state["roi_limit_status"])
    cache_summary = st.session_state.get("api_cache_cleanup_summary")
    if cache_summary:
        with st.expander("Limpeza de cache das APIs", expanded=False):
            st.json(cache_summary)

    st.markdown("#### Diagnóstico técnico")
    with st.expander("Diagnóstico de autenticação Earth Engine", expanded=False):
        st.json(gee_diagnostics())

    catalog = st.session_state.get("gee_catalog")
    if not catalog:
        catalog = load_gee_catalog()
        st.session_state["gee_catalog"] = catalog
    with st.expander("Catálogo GE carregado", expanded=False):
        st.json(catalog)

    operational_log = st.session_state.get("operational_log", [])
    if operational_log:
        with st.expander("Log operacional das fontes", expanded=False):
            st.dataframe(operational_log, use_container_width=True, hide_index=True)
    else:
        st.caption("Nenhum log operacional de fontes foi gerado nesta sessão.")

RISK_COLORS = {
    "Baixo": {"bg": "#14532d", "border": "#22c55e", "text": "#dcfce7"},
    "Moderado": {"bg": "#f59e0b", "border": "#fde68a", "text": "#111827"},
    "Alto": {"bg": "#c2410c", "border": "#fb923c", "text": "#fff7ed"},
    "Muito alto": {"bg": "#dc2626", "border": "#fecaca", "text": "#fef2f2"},
    "Sem dados": {"bg": "#1f2937", "border": "#64748b", "text": "#f8fafc"},
    "Nao calculado": {"bg": "#1f2937", "border": "#64748b", "text": "#f8fafc"},
}


def selected_farms_label(gdf, selected_companies) -> str:
    if not selected_companies:
        return "Nenhuma empresa selecionada"
    selected = set(selected_companies)
    farms = gdf[gdf["EMPRESA"].astype(str).str.strip().isin(selected)].copy()
    if farms.empty or "FAZENDA" not in farms.columns:
        return ", ".join(selected_companies)
    labels = [
        f"{row.get('EMPRESA', '')} / {row.get('FAZENDA', '')}"
        for _, row in farms[["EMPRESA", "FAZENDA"]].drop_duplicates().head(8).iterrows()
    ]
    suffix = "" if len(farms[["EMPRESA", "FAZENDA"]].drop_duplicates()) <= 8 else " ..."
    return "; ".join(labels) + suffix


def render_siren_alert(summary: dict) -> None:
    if not summary.get("fire_alert"):
        return
    distance = summary.get("fire_alert_min_distance_km")
    threshold = summary.get("fire_alert_threshold_km")
    row = summary.get("fire_alert_row") or {}
    st.warning(
        "Alerta sonoro: foco ou anomalia dentro do limite definido. "
        f"Fazenda: {row.get('fazenda', '-')}. UF: {row.get('uf', '-')}. "
        f"Distancia: {distance:.2f} km. Limite: {threshold:.1f} km."
    )
    if not st.session_state.get("use_current_datetime", True):
        st.caption("Alerta sonoro desligado para analises com data/hora manual.")
        return
    st.audio(build_siren_wav(), format="audio/wav", autoplay=True)
    st.caption("Se o navegador bloquear autoplay, use o controle acima para tocar a sirene.")


@st.cache_data(show_spinner=False)
def build_siren_wav(duration_seconds: float = 5.0, sample_rate: int = 22050) -> bytes:
    buffer = BytesIO()
    amplitude = 14000
    phase = 0.0
    total_samples = int(duration_seconds * sample_rate)
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        for sample_index in range(total_samples):
            t = sample_index / sample_rate
            freq = 760 if int(t * 2) % 2 == 0 else 1180
            envelope = min(1.0, t / 0.08, (duration_seconds - t) / 0.18)
            phase += 2 * math.pi * freq / sample_rate
            value = int(amplitude * max(envelope, 0.0) * math.sin(phase))
            wav_file.writeframes(struct.pack("<h", value))
    return buffer.getvalue()


@st.cache_data(show_spinner=False)
def build_refresh_beep_wav(duration_seconds: float = 1.0, sample_rate: int = 22050) -> bytes:
    buffer = BytesIO()
    amplitude = 3200
    frequency = 880
    total_samples = int(duration_seconds * sample_rate)
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        for sample_index in range(total_samples):
            t = sample_index / sample_rate
            envelope = min(1.0, t / 0.04, (duration_seconds - t) / 0.12)
            value = int(amplitude * max(envelope, 0.0) * math.sin(2 * math.pi * frequency * t))
            wav_file.writeframes(struct.pack("<h", value))
    return buffer.getvalue()


def render_auto_refresh_beep() -> None:
    if not st.session_state.get("use_current_datetime", True) or not st.session_state.get("auto_refresh_current_datetime"):
        st.session_state.pop("auto_refresh_beep_pending", None)
        return
    pending = st.session_state.get("auto_refresh_beep_pending")
    if not pending:
        return
    if st.session_state.get("auto_refresh_beep_played") == pending:
        return
    st.session_state["auto_refresh_beep_played"] = pending
    st.audio(build_refresh_beep_wav(), format="audio/wav", autoplay=True)


def clear_authenticated_session(message: str | None = None) -> None:
    st.session_state.pop("auth_user", None)
    st.session_state.pop("session_started_at", None)
    st.session_state.pop("session_local_date", None)
    st.session_state.pop("api_cache_cleaned_for_session", None)
    st.session_state.pop("api_cache_cleanup_summary", None)
    st.session_state.pop("auto_refresh_beep_pending", None)
    st.session_state.pop("auto_refresh_beep_played", None)
    if message:
        st.session_state["auth_notice"] = message


def enforce_midnight_logout() -> bool:
    current_day = now_local().date().isoformat()
    session_day = st.session_state.setdefault("session_local_date", current_day)
    if session_day == current_day:
        return False
    clear_authenticated_session("Sessao encerrada automaticamente na virada do dia. Faca login novamente.")
    return True


@st.fragment(run_every="60s")
def render_session_keepalive() -> None:
    st.session_state["last_session_keepalive"] = datetime.now(timezone.utc).isoformat()
    if enforce_midnight_logout():
        st.rerun(scope="app")
    st.empty()


@st.fragment(run_every="1s")
def render_auto_refresh_scheduler(gdf) -> None:
    if enforce_midnight_logout():
        st.rerun(scope="app")
    if maybe_auto_refresh_analysis(gdf):
        st.rerun(scope="app")


def focus_bounds(lat: float, lon: float, buffer_km: float = 2.0) -> list[list[float]]:
    buffer_m = buffer_km * 1000
    west, _, _ = GEOD.fwd(lon, lat, 270, buffer_m)
    east, _, _ = GEOD.fwd(lon, lat, 90, buffer_m)
    _, south, _ = GEOD.fwd(lon, lat, 180, buffer_m)
    _, north, _ = GEOD.fwd(lon, lat, 0, buffer_m)
    return [[float(south), float(west)], [float(north), float(east)]]


def apply_hotspot_focus(row: dict) -> None:
    try:
        lat = float(row.get("latitude_foco"))
        lon = float(row.get("longitude_foco"))
    except Exception:
        return
    signature = f"{lat:.6f},{lon:.6f},{row.get('fazenda', '')},{row.get('tipo', '')}"
    if st.session_state.get("hotspot_focus_signature") == signature:
        return
    st.session_state["hotspot_focus_signature"] = signature
    st.session_state["hotspot_focus"] = {
        "lat": lat,
        "lon": lon,
        "empresa": row.get("empresa", ""),
        "fazenda": row.get("fazenda", ""),
        "municipio": row.get("municipio", ""),
        "uf": row.get("uf", ""),
        "satelite": row.get("satelite", ""),
        "tipo": row.get("tipo", ""),
        "distancia_km": row.get("distancia_km", ""),
    }
    st.session_state["viewport_fit_bounds"] = focus_bounds(lat, lon)
    st.session_state["fit_viewport_on_next_map"] = True


def distance_row_color(row) -> list[str]:
    try:
        distance = float(row.get("Distancia (km)", 999999))
    except Exception:
        distance = 999999
    if str(row.get("Alerta vento", "")).lower() == "sim":
        background = "#fecaca"
        color = "#7f1d1d"
    elif distance < 0:
        background = "#7f1d1d"
        color = "#ffffff"
    elif distance <= 5:
        background = "#fed7aa"
        color = "#7c2d12"
    elif distance <= 10:
        background = "#fef9c3"
        color = "#713f12"
    else:
        background = "#dcfce7"
        color = "#14532d"
    return [f"background-color: {background}; color: {color};" for _ in row]


def dataframe_to_excel_bytes(df: pd.DataFrame, sheet_name: str) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name[:31])
    return output.getvalue()


def distance_table_rows(items: list[dict]) -> list[dict]:
    return [
        {
            "Fazenda": item.get("fazenda", ""),
            "Distancia (km)": item.get("distancia_km", ""),
            "Vento para fazenda": item.get("vento_para_fazenda", "Sem dados"),
            "Velocidade vento (km/h)": item.get("vento_velocidade_kmh", ""),
            "Direcao vento": item.get("vento_direcao", ""),
            "Data/hora deteccao": item.get("data_hora_deteccao", ""),
            "Data/hora deteccao Zulu": item.get("data_hora_deteccao_zulu", ""),
            "Empresa": item.get("empresa", ""),
            "Municipio": item.get("municipio", ""),
            "UF": item.get("uf", ""),
            "Satelite": item.get("satelite", ""),
            "Tipo hotspot": item.get("tipo", ""),
            "Tipo deteccao": item.get("geometria_deteccao", ""),
            "Geometria": item.get("geometria_deteccao", ""),
            "Limite alerta (km)": item.get("distancia_alerta_km", ""),
            "Limite tabela (km)": item.get("distancia_tabela_km", ""),
            "Alerta sonoro": "Sim" if item.get("alerta_sonoro") else "Nao",
            "Alerta vento": "Sim" if item.get("alerta_vento") else "Nao",
            "Alinhamento vento (graus)": item.get("vento_alinhamento_graus", ""),
            "Rumo foco-fazenda (graus)": item.get("rumo_foco_fazenda_graus", ""),
            "Fonte vento": item.get("fonte_vento", ""),
            "Periodo deteccao": item.get("periodo_deteccao", ""),
            "Latitude": item.get("latitude_foco", ""),
            "Longitude": item.get("longitude_foco", ""),
        }
        for item in items
    ]


def grouped_distance_rows(nearest: list[dict]) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = {}
    for item in nearest:
        farm_name = str(item.get("fazenda") or "Sem fazenda").strip() or "Sem fazenda"
        groups.setdefault(farm_name, []).append(item)
    grouped = []
    for farm_name, items in groups.items():
        ordered_items = sorted(
            items,
            key=lambda item: (float(item.get("distancia_km", 999999) or 999999), int(item.get("priority", 99) or 99)),
        )
        grouped.append((farm_name, ordered_items))
    return sorted(
        grouped,
        key=lambda group: (
            float(group[1][0].get("distancia_km", 999999) or 999999),
            int(group[1][0].get("priority", 99) or 99),
        ),
    )


def group_alert_state(items: list[dict]) -> dict:
    fire_limit_rows = []
    wind_rows = []
    for item in items:
        try:
            distance = float(item.get("distancia_km", 999999) or 999999)
            limit = float(item.get("distancia_alerta_km", 0) or 0)
        except Exception:
            distance = 999999
            limit = 0
        if limit > 0 and distance <= limit:
            fire_limit_rows.append(item)
        if item.get("alerta_vento"):
            wind_rows.append(item)
    if fire_limit_rows:
        return {
            "level": "fire",
            "label": "Dentro do limite de alerta",
            "bg": "#7f1d1d",
            "border": "#ef4444",
            "text": "#fff7ed",
        }
    if wind_rows:
        return {
            "level": "wind",
            "label": "Vento direcionado <= 5 km",
            "bg": "#fed7aa",
            "border": "#fb923c",
            "text": "#7c2d12",
        }
    return {
        "level": "normal",
        "label": "Monitorado",
        "bg": "transparent",
        "border": "rgba(148, 163, 184, 0.25)",
        "text": "inherit",
    }


def render_grouped_distance_table(nearest: list[dict]) -> None:
    grouped = grouped_distance_rows(nearest)
    expand_table = st.toggle(
        f"Expandir todas as fazendas ({len(grouped)} grupos / {len(nearest)} incidencias)",
        value=st.session_state.get("expand_fire_distance_table", False),
        key="expand_fire_distance_table",
    )
    visible_groups = grouped if expand_table else grouped[:10]
    if not expand_table and len(grouped) > 10:
        st.caption(f"Exibindo as 10 fazendas mais proximas de {len(grouped)} grupos calculados.")

    export_df = pd.DataFrame(distance_table_rows(nearest))
    st.download_button(
        "Exportar distancias para Excel",
        data=dataframe_to_excel_bytes(export_df, "Distancias"),
        file_name="distancias_focos_hotspots.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=False,
    )

    for group_index, (farm_name, items) in enumerate(visible_groups):
        first = items[0]
        min_distance = float(first.get("distancia_km", 999999) or 999999)
        company = first.get("empresa", "")
        state = group_alert_state(items)
        if state["level"] != "normal":
            st.markdown(
                f"""
                <div style="
                    background:{state['bg']};
                    color:{state['text']};
                    border:1px solid {state['border']};
                    border-radius:8px;
                    padding:8px 12px;
                    margin:8px 0 4px 0;
                    font-weight:800;">
                    {farm_name} | {min_distance:.2f} km | {company} |
                    {len(items)} incidencia(s) | {state['label']}
                </div>
                """,
                unsafe_allow_html=True,
            )
        label_suffix = f" | {state['label']}" if state["level"] != "normal" else ""
        label = f"{farm_name} | {min_distance:.2f} km | {company} | {len(items)} incidencia(s){label_suffix}"
        with st.expander(label, expanded=group_index == 0):
            group_df = pd.DataFrame(distance_table_rows(items))
            selection = st.dataframe(
                group_df.style.apply(distance_row_color, axis=1),
                use_container_width=True,
                hide_index=True,
                key=f"fire_distance_group_{group_index}_{abs(hash(farm_name))}",
                on_select="rerun",
                selection_mode="single-row",
            )
            selected_rows = getattr(getattr(selection, "selection", None), "rows", [])
            if selected_rows:
                selected_index = int(selected_rows[0])
                if 0 <= selected_index < len(items):
                    apply_hotspot_focus(items[selected_index])


def render_fire_detection_panel(gdf, selected_companies) -> None:
    summary = st.session_state.get("fire_detection_summary", {})
    if not summary:
        st.info("Aplique uma ROI para gerar o painel de risco e focos de calor.")
        return

    risk_value = summary.get("risk_value")
    risk_label = summary.get("risk_class", "Sem dados")
    risk_display = f"{risk_value:.1f}" if isinstance(risk_value, (int, float)) else "-"
    risk_style = RISK_COLORS.get(risk_label, RISK_COLORS["Sem dados"])

    st.markdown("### Risco de incêndios florestais para as fazendas selecionadas")
    st.caption(f"Fazendas selecionadas: {selected_farms_label(gdf, selected_companies)}")
    st.caption(f"Data e hora da análise: {st.session_state.get('analysis_reference_label', '-')}")
    image_rows = st.session_state.get("analysis_image_rows", [])
    if image_rows:
        with st.expander("Imagens e dados usados na análise", expanded=False):
            st.dataframe(image_rows, use_container_width=True, hide_index=True)
        image_rows = []
    if image_rows:
        st.markdown("#### Imagens e dados usados na análise")
        st.dataframe(image_rows, use_container_width=True, hide_index=True)

    render_siren_alert(summary)
    wind_context = summary.get("wind_context") or {}
    if wind_context:
        speed = wind_context.get("speed_kmh", "-")
        direction = wind_context.get("direction_deg", "-")
        wind_alert_count = int(summary.get("wind_alert_count", 0) or 0)
        st.caption(
            f"Vento de referencia: {speed} km/h, direcao {direction} graus "
            f"({wind_context.get('source', 'fonte meteorologica')}). "
            f"Fazendas com foco <= 5 km e vento direcionado: {wind_alert_count}."
        )

    st.markdown(
        f"""
        <div style="
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap:18px;
            width:100%;
            background:{risk_style['bg']};
            border:1px solid {risk_style['border']};
            color:{risk_style['text']};
            border-radius:10px;
            padding:10px 16px;
            margin:8px 0 12px 0;">
            <div style="font-size:13px; font-weight:700; opacity:.92;">Grau de risco</div>
            <div style="display:flex; align-items:baseline; gap:12px;">
                <span style="font-size:26px; font-weight:850; line-height:1;">{risk_display}</span>
                <span style="font-size:15px; font-weight:800;">{risk_label}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    nearest = sorted(
        summary.get("nearest_farms", []),
        key=lambda item: (float(item.get("distancia_km", 999999) or 999999), int(item.get("priority", 99) or 99)),
    )
    if nearest:
        st.markdown("#### Distancias ate focos, hotspots e anomalias")
        st.caption("Abra uma fazenda para ver todas as incidencias dos satelites. Selecione uma linha para aproximar o mapa no foco correspondente.")
        render_grouped_distance_table(nearest)
        if summary.get("status"):
            st.caption(summary["status"])
        return
        st.caption("Selecione ou dê dois cliques em uma linha para aproximar o mapa no foco correspondente.")
        expand_table = st.toggle(
            f"Expandir tabela completa ({len(nearest)} itens)",
            value=st.session_state.get("expand_fire_distance_table", False),
            key="expand_fire_distance_table",
        )
        visible_nearest = nearest if expand_table else nearest[:10]
        if not expand_table and len(nearest) > 10:
            st.caption(f"Exibindo os 10 focos mais proximos de {len(nearest)} registros calculados.")
        def distance_table_rows(items: list[dict]) -> list[dict]:
            return [
                {
                    "Empresa": item.get("empresa", ""),
                    "Fazenda": item.get("fazenda", ""),
                    "Municipio": item.get("municipio", ""),
                    "UF": item.get("uf", ""),
                    "Satelite": item.get("satelite", ""),
                    "Tipo": item.get("tipo", ""),
                    "Geometria": item.get("geometria_deteccao", ""),
                    "Distancia (km)": item.get("distancia_km", ""),
                    "Limite alerta (km)": item.get("distancia_alerta_km", ""),
                    "Alerta sonoro": "Sim" if item.get("alerta_sonoro") else "Nao",
                    "Latitude": item.get("latitude_foco", ""),
                    "Longitude": item.get("longitude_foco", ""),
                }
                for item in items
            ]

        table_rows = distance_table_rows(visible_nearest)
        export_df = pd.DataFrame(distance_table_rows(nearest))
        st.download_button(
            "Exportar distancias para Excel",
            data=dataframe_to_excel_bytes(export_df, "Distancias"),
            file_name="distancias_focos_hotspots.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=False,
        )
        table_df = pd.DataFrame(table_rows)
        selection = st.dataframe(
            table_df.style.apply(distance_row_color, axis=1),
            use_container_width=True,
            hide_index=True,
            key="fire_distance_table",
            on_select="rerun",
            selection_mode="single-row",
        )
        selected_rows = getattr(getattr(selection, "selection", None), "rows", [])
        if selected_rows:
            selected_index = int(selected_rows[0])
            if 0 <= selected_index < len(visible_nearest):
                apply_hotspot_focus(visible_nearest[selected_index])
    else:
        points_total = int(summary.get("points_total", 0) or 0)
        if points_total == 0:
            st.caption(
                "Nenhum foco, hotspot, anomalia ou poligono de deteccao foi amostrado nas camadas selecionadas; "
                "por isso nao ha distancia ate fazenda para calcular nesta consulta."
            )
        else:
            st.caption(
                f"{points_total} deteccao(oes) foram amostradas, mas nao foi possivel consolidar "
                "distancias para as fazendas selecionadas."
            )

    if summary.get("status"):
        st.caption(summary["status"])


def main() -> None:
    apply_styles()
    user = require_authentication()
    if enforce_midnight_logout():
        st.rerun()
    ensure_api_cache_cleaned_for_session(st.session_state)
    render_top_header(user)
    render_session_keepalive()

    try:
        gdf = load_farms()
    except Exception as exc:
        st.error(exc)
        st.stop()

    selected_companies, range_km = render_sidebar(gdf)

    action_cols = st.columns([0.78, 0.12, 0.10])
    with action_cols[0]:
        company_label = (
            f"Empresa ativa: {selected_companies[0]}"
            if len(selected_companies) == 1
            else f"Empresas selecionadas: {len(selected_companies)}"
        )
        st.caption(
            f"{company_label} | "
            f"Camadas GE aplicadas: {len(st.session_state.get('gee_applied_indicators', []))}"
        )
    with action_cols[2]:
        if st.button("Sair", use_container_width=True):
            clear_authenticated_session()
            st.rerun()

    main_tabs = [
        "Mapa Operacional",
        "Previsao do Tempo",
        "Tendencia Climatica",
        "Log Técnico",
    ]
    if st.session_state.get("active_main_tab") not in main_tabs:
        st.session_state["active_main_tab"] = "Mapa Operacional"
    main_tab = st.radio(
        "Area principal",
        main_tabs,
        horizontal=True,
        key="active_main_tab",
    )
    render_auto_refresh_scheduler(gdf)
    maybe_auto_refresh_analysis(gdf)

    if main_tab == "Mapa Operacional":
        render_fire_detection_panel(gdf, selected_companies)
        map_output = build_main_map(
            gdf,
            selected_companies,
            range_km,
            capture_clicks=False,
        )
        render_auto_refresh_beep()
    elif main_tab == "Previsao do Tempo":
        render_weather_forecast_tab()
    elif main_tab == "Tendencia Climatica":
        render_climate_trend_tab()
    else:
        render_technical_log(selected_companies)


if __name__ == "__main__":
    main()
