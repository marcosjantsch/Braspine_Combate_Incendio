# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, time, timezone
from typing import List, Tuple

import streamlit as st
import streamlit.components.v1 as components

from core.config import DEFAULT_RANGE_KM, SATELLITE_DESCRIPTIONS, SATELLITE_OPTIONS
from core.geometry_service import (
    parse_decimal_degrees,
    parse_dms_pair,
    sirgas_utm_to_decimal_degrees,
    utm_to_decimal_degrees,
)
from core.roi_service import build_project_roi, session_roi
from core.time_context import (
    LOCAL_TZ,
    format_datetime_brasilia,
    format_datetime_zulu,
    format_period_brasilia,
    format_period_zulu,
    now_local,
    selected_analysis_label,
    selected_analysis_midpoint_utc,
    selected_analysis_window_utc,
    selected_datetime_iso,
    selected_datetime_local,
    set_manual_date,
)
from services.gee_service import load_gee_catalog
from services.fire_risk_service import build_fire_risk_index
from services.fire_sources_service import (
    CURRENT_ACTIVE_FIRE_WINDOW_HOURS,
    compute_hotspot_distances,
    fetch_selected_sources,
    classify_alert_level,
)
from services.weather_service import fetch_weather_window, weather_code_label


RISK_INDICATOR = "Risco de incendio florestal"
GOES_VISUAL_INDICATOR = "GOES visual meteorologico"
GOES_THERMAL_INDICATOR = "GOES temperatura de brilho"
GOES_HOTSPOT_INDICATOR = "GOES hotspots recentes"
GOES_INDICATORS = {GOES_VISUAL_INDICATOR, GOES_THERMAL_INDICATOR, GOES_HOTSPOT_INDICATOR}
DEFAULT_GEE_INDICATORS = [
    "Risco de incendio florestal",
    "GOES hotspots recentes",
    "NASA GIBS Hotspots",
    "INPE Queimadas",
    "FIRMS MODIS",
    "VIIRS 375 m",
    "MODIS Terra FireMask",
    "NOAA HMS Smoke",
    "CAMS aerossois/fumaca",
]
AUTO_REFRESH_INTERVAL_SECONDS = 15 * 60
AUTO_REFRESH_INTERVAL_MS = AUTO_REFRESH_INTERVAL_SECONDS * 1000
AUTO_REFRESH_INTERVAL_LABEL = "15 minutos"
ORBITAL_REFRESH_WINDOWS = [
    (time(9, 50), time(11, 50)),
    (time(13, 20), time(15, 10)),
]
ORBITAL_WINDOW_LABEL = "09:50-11:50 e 13:20-15:10"
ORBITAL_REFRESH_CHECKPOINTS = [time(9, 50), time(11, 50), time(13, 20), time(15, 10)]
ORBITAL_REFRESH_INDICATORS = {
    "FIRMS MODIS",
    "VIIRS 375 m",
    "NASA GIBS Hotspots",
    "MODIS Terra FireMask",
    "MODIS Burned Area",
    "NOAA HMS Smoke",
    "CAMS aerossois/fumaca",
    "Sentinel-3 SLSTR",
    "Landsat Collection 2 Thermal",
    "Sentinel-2 NDVI/NBR",
    "ERA5 Land",
    "ECMWF Fire Weather Index",
    "GOES GLM Lightning",
    "SMAP umidade do solo",
}


def auto_refresh_clock_now() -> datetime:
    return datetime.now(LOCAL_TZ).replace(microsecond=0)


def is_orbital_refresh_window(moment: datetime | None = None) -> bool:
    current_time = (moment or auto_refresh_clock_now()).astimezone(LOCAL_TZ).time()
    return any(start <= current_time <= end for start, end in ORBITAL_REFRESH_WINDOWS)


def orbital_checkpoint_due(moment: datetime) -> str:
    current = moment.astimezone(LOCAL_TZ)
    last_checkpoint = st.session_state.get("last_orbital_checkpoint")
    due_checkpoint = ""
    for checkpoint in ORBITAL_REFRESH_CHECKPOINTS:
        checkpoint_dt = current.replace(
            hour=checkpoint.hour,
            minute=checkpoint.minute,
            second=0,
            microsecond=0,
        )
        checkpoint_id = checkpoint_dt.strftime("%Y-%m-%d %H:%M")
        if current >= checkpoint_dt:
            due_checkpoint = checkpoint_id
    if due_checkpoint and due_checkpoint != last_checkpoint:
        return due_checkpoint
    return ""


def orbital_sources_found(source_results: list[dict]) -> bool:
    for result in source_results or []:
        source = result.get("source", {})
        if source.get("label") in ORBITAL_REFRESH_INDICATORS and result.get("status") == "plotado" and int(result.get("count", 0) or 0) > 0:
            return True
    return False


def update_satellite_last_image_log(source_results: list[dict], only_orbital: bool = False) -> bool:
    log = dict(st.session_state.get("satellite_last_image_log", {}))
    changed = False
    for result in source_results or []:
        source = result.get("source", {})
        label = str(source.get("label") or result.get("source_key") or "").strip()
        if not label:
            continue
        if only_orbital and label not in ORBITAL_REFRESH_INDICATORS:
            continue
        if result.get("status") != "plotado" or int(result.get("count", 0) or 0) <= 0:
            continue
        signature = {
            "image_datetime": result.get("image_datetime") or "",
            "count": int(result.get("count", 0) or 0),
            "updated_at": auto_refresh_clock_now().isoformat(),
        }
        previous = log.get(label, {})
        if previous.get("image_datetime") != signature["image_datetime"] or previous.get("count") != signature["count"]:
            changed = True
            log[label] = signature
    st.session_state["satellite_last_image_log"] = log
    return changed


def scheduled_auto_refresh_indicators(applied_indicators: List[str], moment: datetime) -> tuple[List[str], str]:
    applied = list(dict.fromkeys(applied_indicators or []))
    if not st.session_state.get("auto_refresh_full_satellite_query_done"):
        return applied, "Consulta automatica inicial da sessao: todas as camadas aplicadas foram reconsultadas."

    selected_goes = [name for name in applied if name in GOES_INDICATORS]
    selected_orbital = [name for name in applied if name in ORBITAL_REFRESH_INDICATORS]
    checkpoint_id = orbital_checkpoint_due(moment)
    pending_checkpoint = st.session_state.get("pending_orbital_checkpoint")
    if checkpoint_id and selected_orbital:
        st.session_state["pending_orbital_checkpoint"] = checkpoint_id
        st.session_state["last_orbital_checkpoint"] = checkpoint_id
        pending_checkpoint = checkpoint_id

    if pending_checkpoint:
        selected = list(dict.fromkeys([*selected_goes, *selected_orbital]))
        return selected, f"Consulta orbital pendente desde {pending_checkpoint}; GOES e satelites de passagem foram reconsultados."

    selected = selected_goes
    return selected, f"Fora dos horarios orbitais programados ({ORBITAL_WINDOW_LABEL}); apenas camadas GOES foram reconsultadas."


def _roi_center_from_bounds(bounds) -> tuple[float, float] | None:
    try:
        south, west = bounds[0]
        north, east = bounds[1]
        return (float(south) + float(north)) / 2.0, (float(west) + float(east)) / 2.0
    except Exception:
        return None


def _context_value(values: list, index: int):
    return values[index] if index < len(values) else None


def _context_float(values: list, index: int) -> float | None:
    value = _context_value(values, index)
    try:
        return float(value)
    except Exception:
        return None


def _weather_condition_label(weather_code, cloud_cover, precipitation, humidity) -> str:
    label = weather_code_label(weather_code)
    if label and label != "-":
        if label == "Ceu limpo":
            return "Ensolarado"
        return label

    if precipitation is not None and precipitation > 0.2:
        return "Chovendo"
    if humidity is not None and humidity >= 95 and (cloud_cover is None or cloud_cover >= 50):
        return "Neblina"
    if cloud_cover is not None and cloud_cover >= 80:
        return "Nublado"
    if cloud_cover is not None and cloud_cover >= 35:
        return "Parcialmente nublado"
    if cloud_cover is not None:
        return "Ensolarado"
    return "-"


@st.cache_data(ttl=900, show_spinner=False)
def _cached_wind_context(lat: float, lon: float, reference_local_iso: str) -> dict:
    reference = datetime.fromisoformat(reference_local_iso)
    payload = fetch_weather_window(float(lat), float(lon), reference.date(), days=1)
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    temperatures = hourly.get("temperature_2m") or []
    weather_codes = hourly.get("weather_code") or []
    cloud_covers = hourly.get("cloud_cover") or []
    precipitations = hourly.get("precipitation") or []
    humidities = hourly.get("relative_humidity_2m") or []
    speeds = hourly.get("wind_speed_10m") or []
    directions = hourly.get("wind_direction_10m") or []
    if not times:
        return {"status": "Vento indisponivel: sem dados horarios.", "source": "Open-Meteo centro da ROI"}

    best_index = 0
    best_delta = None
    for idx, value in enumerate(times):
        try:
            hour = datetime.fromisoformat(str(value))
            delta = abs((hour - reference.replace(tzinfo=None)).total_seconds())
        except Exception:
            continue
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_index = idx

    speed = _context_value(speeds, best_index)
    direction = _context_value(directions, best_index)
    temperature = _context_value(temperatures, best_index)
    weather_code = _context_value(weather_codes, best_index)
    cloud_cover = _context_float(cloud_covers, best_index)
    precipitation = _context_float(precipitations, best_index)
    humidity = _context_float(humidities, best_index)
    return {
        "temperature_c": temperature,
        "weather_code": weather_code,
        "weather_condition": _weather_condition_label(weather_code, cloud_cover, precipitation, humidity),
        "cloud_cover_pct": cloud_cover,
        "precipitation_mm": precipitation,
        "humidity_pct": humidity,
        "speed_kmh": speed,
        "direction_deg": direction,
        "time": times[best_index],
        "source": "Open-Meteo centro da ROI",
        "status": f"Vento carregado no centro da ROI em {times[best_index]}.",
    }


def build_wind_context(roi_bounds) -> dict:
    center = _roi_center_from_bounds(roi_bounds)
    if not center:
        return {"status": "Vento indisponivel: ROI sem centro calculado.", "source": "Open-Meteo centro da ROI"}
    try:
        lat, lon = center
        if st.session_state.get("use_current_datetime", True):
            reference_local = selected_datetime_local()
        else:
            reference_local = selected_analysis_midpoint_utc().astimezone(LOCAL_TZ)
        return _cached_wind_context(round(lat, 5), round(lon, 5), reference_local.isoformat())
    except Exception as exc:
        return {"status": f"Vento indisponivel: {exc}", "source": "Open-Meteo centro da ROI"}


def build_coordinate_wind_context(lat: float, lon: float) -> dict:
    try:
        if st.session_state.get("use_current_datetime", True):
            reference_local = selected_datetime_local()
        else:
            reference_local = selected_analysis_midpoint_utc().astimezone(LOCAL_TZ)
        context = _cached_wind_context(round(float(lat), 5), round(float(lon), 5), reference_local.isoformat())
        context["source"] = "Open-Meteo coordenada manual"
        if context.get("time"):
            context["status"] = f"Vento carregado na coordenada manual em {context['time']}."
        return context
    except Exception as exc:
        return {"status": f"Vento indisponivel na coordenada manual: {exc}", "source": "Open-Meteo coordenada manual"}


def _parse_decimal_coordinate(value: str, label: str, minimum: float, maximum: float) -> float:
    text = str(value or "").strip().replace(",", ".")
    if not text:
        raise ValueError(f"Informe {label}.")
    number = float(text)
    if number < minimum or number > maximum:
        raise ValueError(f"{label} deve estar entre {minimum} e {maximum}.")
    return number


def manual_coordinate_raw() -> tuple[str, str]:
    mode = st.session_state.get("manual_coordinate_mode", "decimal")
    buffer_m = str(st.session_state.get("manual_coordinate_buffer_m", 5000)).strip()
    if mode == "dms":
        return (
            "dms",
            str(st.session_state.get("manual_coordinate_dms_lat", "")).strip(),
            str(st.session_state.get("manual_coordinate_dms_lon", "")).strip(),
            buffer_m,
        )
    if mode == "utm":
        return (
            "utm",
            str(st.session_state.get("manual_coordinate_utm_easting", "")).strip(),
            str(st.session_state.get("manual_coordinate_utm_northing", "")).strip(),
            str(st.session_state.get("manual_coordinate_utm_zone", "22")).strip(),
            str(st.session_state.get("manual_coordinate_utm_sirgas", True)),
            buffer_m,
        )
    return (
        "decimal",
        str(st.session_state.get("manual_coordinate_lat", "")).strip(),
        str(st.session_state.get("manual_coordinate_lon", "")).strip(),
        buffer_m,
    )


def parse_manual_coordinate_input() -> tuple[float, float]:
    mode = st.session_state.get("manual_coordinate_mode", "decimal")
    if mode == "dms":
        lat_value = str(st.session_state.get("manual_coordinate_dms_lat", "")).strip()
        lon_value = str(st.session_state.get("manual_coordinate_dms_lon", "")).strip()
        if not lat_value or not lon_value:
            raise ValueError("Informe latitude e longitude em graus, minutos e segundos.")
        return parse_dms_pair(lat_value, lon_value)
    if mode == "utm":
        easting = str(st.session_state.get("manual_coordinate_utm_easting", "")).strip().replace(",", ".")
        northing = str(st.session_state.get("manual_coordinate_utm_northing", "")).strip().replace(",", ".")
        zone = int(st.session_state.get("manual_coordinate_utm_zone", 22) or 22)
        if not easting or not northing:
            raise ValueError("Informe Leste/Easting e Norte/Northing UTM.")
        if st.session_state.get("manual_coordinate_utm_sirgas", True):
            return sirgas_utm_to_decimal_degrees(float(easting), float(northing), zone=zone)
        return utm_to_decimal_degrees(float(easting), float(northing), zone=zone, hemisphere="S")
    raw_lat = str(st.session_state.get("manual_coordinate_lat", "")).strip()
    raw_lon = str(st.session_state.get("manual_coordinate_lon", "")).strip()
    if not raw_lat or not raw_lon:
        raise ValueError("Informe latitude e longitude em graus decimais.")
    return parse_decimal_degrees(raw_lat, raw_lon)


def manual_coordinate_detection(lat: float, lon: float) -> dict:
    timestamp_local = selected_datetime_local() if st.session_state.get("use_current_datetime", True) else selected_analysis_midpoint_utc().astimezone(LOCAL_TZ)
    return {
        "lat": float(lat),
        "lon": float(lon),
        "distance_capable": True,
        "alert_capable": False,
        "source": "Coordenada manual",
        "source_key": "manual_coordinate",
        "satellite": "Entrada manual",
        "event_type": "Ponto manual",
        "priority": 98,
        "geometry_type": "point",
        "detection_datetime": format_datetime_brasilia(timestamp_local),
        "detection_datetime_zulu": format_datetime_zulu(timestamp_local),
        "detection_period": "Coordenada digitada pelo usuario",
    }


def apply_manual_coordinate_analysis(gdf, selected_companies: List[str], show_feedback: bool = False) -> None:
    signature = manual_coordinate_raw()
    st.session_state["manual_coordinate_applied_raw"] = signature
    has_value = any(str(part).strip() for part in signature[1:])
    if not has_value:
        st.session_state["manual_coordinate_point"] = None
        st.session_state["manual_coordinate_distance"] = None
        st.session_state["manual_coordinate_wind_context"] = {}
        return

    try:
        lat, lon = parse_manual_coordinate_input()
        if not -90.0 <= float(lat) <= 90.0:
            raise ValueError("Latitude deve estar entre -90 e 90.")
        if not -180.0 <= float(lon) <= 180.0:
            raise ValueError("Longitude deve estar entre -180 e 180.")
    except Exception as exc:
        st.session_state["manual_coordinate_point"] = None
        st.session_state["manual_coordinate_distance"] = None
        st.session_state["manual_coordinate_wind_context"] = {}
        if show_feedback:
            st.warning(f"Coordenada manual invalida: {exc}")
        return

    wind_context = build_coordinate_wind_context(lat, lon)
    rows = compute_hotspot_distances(
        [manual_coordinate_detection(lat, lon)],
        gdf,
        selected_companies or [],
        limit=1,
        max_distance_km=1_000_000.0,
        wind_context=wind_context,
        enforce_table_distance=False,
    )
    result = rows[0] if rows else None
    st.session_state["manual_coordinate_point"] = {"lat": lat, "lon": lon}
    st.session_state["manual_coordinate_distance"] = result
    st.session_state["manual_coordinate_wind_context"] = wind_context
    st.session_state["viewport_fit_bounds"] = [[lat - 0.02, lon - 0.02], [lat + 0.02, lon + 0.02]]
    st.session_state["fit_viewport_on_next_map"] = True
    if show_feedback and result:
        st.success(
            f"Coordenada manual aplicada. Fazenda mais proxima: {result.get('fazenda', '-')}, "
            f"{result.get('distancia_km', '-')} km."
        )


def render_auto_refresh_countdown() -> None:
    last_value = st.session_state.get("last_auto_analysis_refresh") or auto_refresh_clock_now().isoformat()
    ready = bool(
        st.session_state.get("selected_companies")
        and st.session_state.get("gee_applied_indicators")
        and st.session_state.get("gee_roi")
    )
    status = (
        "Consultas aplicadas serao refeitas automaticamente."
        if ready
        else "Aguardando clicar em Aplicar para iniciar o ciclo automatico."
    )
    last_status = st.session_state.get("last_auto_analysis_status")
    if last_status and ready:
        status = str(last_status)
    components.html(
        f"""
        <div class="fire-refresh-card">
            <div class="fire-refresh-label">Proxima atualizacao operacional</div>
            <div class="fire-refresh-count" id="fire-refresh-count">15:00</div>
            <div class="fire-refresh-note" id="fire-refresh-note">{status}</div>
        </div>
        <script>
        const lastRefresh = new Date("{last_value}").getTime();
        const intervalMs = {AUTO_REFRESH_INTERVAL_MS};
        const countEl = document.getElementById("fire-refresh-count");
        const noteEl = document.getElementById("fire-refresh-note");
        function pad(value) {{
            return String(value).padStart(2, "0");
        }}
        function tickRefresh() {{
            const nextRefresh = lastRefresh + intervalMs;
            const remainingMs = Math.max(0, nextRefresh - Date.now());
            const remaining = Math.floor(remainingMs / 1000);
            const minutes = Math.floor(remaining / 60);
            const seconds = remaining % 60;
            countEl.textContent = `${{pad(minutes)}}:${{pad(seconds)}}`;
            if (remaining <= 0) {{
                noteEl.textContent = "Atualizacao programada. A consulta sera refeita pelo servidor.";
            }}
        }}
        tickRefresh();
        window.clearInterval(window.__fireRefreshCountdown);
        window.__fireRefreshCountdown = window.setInterval(tickRefresh, 1000);
        </script>
        <style>
        body {{
            margin: 0;
            background: transparent;
            font-family: "Source Sans Pro", sans-serif;
        }}
        .fire-refresh-card {{
            box-sizing: border-box;
            width: 100%;
            padding: 10px 12px;
            border: 1px solid rgba(52, 211, 153, 0.22);
            border-radius: 12px;
            background: rgba(2, 6, 23, 0.72);
            color: #d1fae5;
        }}
        .fire-refresh-label {{
            font-size: 11px;
            color: #a7f3d0;
            text-transform: uppercase;
            letter-spacing: .04em;
        }}
        .fire-refresh-count {{
            margin-top: 2px;
            color: #ecfdf5;
            font-size: 24px;
            font-weight: 850;
            line-height: 1.1;
        }}
        .fire-refresh-note {{
            margin-top: 4px;
            color: #94a3b8;
            font-size: 11px;
            line-height: 1.35;
        }}
        </style>
        """,
        height=96,
    )


def render_datetime_tab() -> None:
    st.markdown("### Data e hora")
    use_current = st.checkbox(
        "Usar data e hora atual",
        value=st.session_state.get("use_current_datetime", True),
        key="use_current_datetime",
        help="Quando marcado, as consultas buscam os dados mais atuais disponiveis.",
    )
    if use_current:
        current_dt = now_local()
        st.session_state["analysis_datetime_iso"] = current_dt.isoformat()
        st.caption(f"Referencia atual: {format_datetime_brasilia(current_dt)} | {format_datetime_zulu(current_dt)}")
        st.checkbox(
            f"Atualizar automaticamente a cada {AUTO_REFRESH_INTERVAL_LABEL}",
            value=st.session_state.get("auto_refresh_current_datetime", False),
            key="auto_refresh_current_datetime",
            help=f"Quando ativo, a ROI aplicada e as camadas selecionadas sao recalculadas a cada {AUTO_REFRESH_INTERVAL_LABEL}.",
        )
        if st.session_state.get("auto_refresh_current_datetime"):
            st.caption(f"Atualizacao automatica ativa: as consultas aplicadas serao refeitas a cada {AUTO_REFRESH_INTERVAL_LABEL}.")
            render_auto_refresh_countdown()
        return

    current_dt = selected_datetime_local()
    selected_day = st.date_input("Data de referencia", value=current_dt.date(), key="analysis_date_input")
    set_manual_date(selected_day)
    start_utc, end_utc = selected_analysis_window_utc()
    st.caption(
        "Periodo manual: "
        f"{format_period_brasilia(start_utc, end_utc)} | {format_period_zulu(start_utc, end_utc)}"
    )


def render_project_tab(gdf) -> List[str]:
    st.markdown("### Empresas")
    companies = sorted(str(value).strip() for value in gdf["EMPRESA"].dropna().unique())
    current = set(st.session_state.get("selected_companies", []))
    selected = []
    st.caption("Marque as empresas do projeto. O processamento ocorre no botao Aplicar abaixo da secao GE.")
    for company in companies:
        if st.checkbox(company, value=company in current, key=f"company_{company}"):
            selected.append(company)
    st.session_state["pending_selected_companies"] = selected
    st.session_state["show_original_polygons"] = st.checkbox(
        "Exibir poligonos sem simplificacao",
        value=st.session_state.get("show_original_polygons", False),
        help="Mostra as geometrias completas do shapefile. Pode deixar o mapa mais lento.",
    )
    st.session_state["show_map_legend"] = st.checkbox(
        "Exibir legenda no mapa operacional",
        value=st.session_state.get("show_map_legend", True),
        help="Liga ou desliga a legenda fixa exibida no canto inferior do mapa operacional.",
    )
    return selected


def apply_company_selection(gdf, selected: List[str], fit_map: bool = False) -> None:
    st.session_state["selected_companies"] = selected
    st.session_state["fit_company_on_next_map"] = False


def apply_sidebar_selection(gdf, selected_companies: List[str], selected_indicators: List[str]) -> None:
    selected_companies = list(dict.fromkeys(selected_companies or []))
    selected_indicators = list(dict.fromkeys(selected_indicators or []))
    apply_company_selection(gdf, selected_companies, fit_map=False)
    st.session_state["applied_company_selection"] = selected_companies
    st.session_state.pop("hotspot_focus", None)
    st.session_state.pop("hotspot_focus_signature", None)

    roi_result = build_project_roi(gdf, selected_companies)
    st.session_state["project_roi_result"] = roi_result
    st.session_state["roi_limit_status"] = roi_result["status"]
    if roi_result["ok"]:
        st.session_state["viewport_fit_bounds"] = roi_result["bounds"]
        st.session_state["fit_viewport_on_next_map"] = True
        st.session_state["last_auto_analysis_refresh"] = auto_refresh_clock_now().isoformat()
        st.session_state["auto_refresh_full_satellite_query_done"] = True
        st.session_state.pop("pending_orbital_checkpoint", None)
        apply_fire_risk_and_goes(
            selected_indicators,
            roi_result=roi_result,
            gdf=gdf,
            selected_companies=selected_companies,
        )
    else:
        st.session_state["gee_applied_indicators"] = []
        st.session_state["gee_roi"] = None
        st.session_state["roi_ee"] = None
        st.session_state["roi_bounds"] = None
        st.session_state["applied_roi_bounds"] = None
        st.session_state["gee_tile_layers"] = []
        st.session_state["fire_risk_layers"] = []
        st.session_state["fire_detection_summary"] = {}
        st.session_state["day_detection_rows"] = []
        st.session_state["day_detection_points"] = []
        st.session_state["day_detection_points_total"] = 0
        st.session_state["day_detection_status"] = ""
        st.session_state["day_detection_logs"] = []
        st.session_state["day_detection_period"] = ""
        st.warning(roi_result["status"])
    st.session_state["active_main_tab"] = "Mapa Operacional"


def current_map_roi() -> dict | None:
    return session_roi(st.session_state)


def _analysis_image_rows(applied_indicators: List[str], source_rows: List[dict]) -> List[dict]:
    if st.session_state.get("use_current_datetime", True):
        reference_label = format_datetime_brasilia(selected_datetime_local())
        reference_zulu = format_datetime_zulu(selected_analysis_midpoint_utc())
        risk_period_label = f"30 dias ate {reference_label}"
        risk_period_zulu = f"30 dias ate {reference_zulu}"
        risk_datetime_label = f"Composicao multi-fonte ate {reference_label}"
        risk_datetime_zulu = f"Composicao multi-fonte ate {reference_zulu}"
    else:
        start_utc, end_utc = selected_analysis_window_utc()
        reference_label = format_period_brasilia(start_utc, end_utc)
        reference_zulu = format_period_zulu(start_utc, end_utc)
        risk_period_label = f"30 dias ate o fim do dia selecionado ({format_datetime_brasilia(end_utc)})"
        risk_period_zulu = f"30 dias ate o fim do dia selecionado ({format_datetime_zulu(end_utc)})"
        risk_datetime_label = f"Composicao multi-fonte para o dia selecionado ({reference_label})"
        risk_datetime_zulu = f"Composicao multi-fonte para o dia selecionado ({reference_zulu})"
    rows = []
    if RISK_INDICATOR in applied_indicators:
        rows.append(
            {
                "Camada": "Indice de risco",
                "Fonte": "ERA5 Land, MODIS LST, Sentinel-2 e VIIRS",
                "Data/hora Brasilia": risk_datetime_label,
                "Data/hora Zulu": risk_datetime_zulu,
                "Periodo usado Brasilia": risk_period_label,
                "Periodo usado Zulu": risk_period_zulu,
                "Como foi plotado": "Periodo climatico/vegetacao usado apenas no painel de risco",
            }
        )
    rows.extend(source_rows)
    return rows


def _analysis_reference_payload() -> tuple[str | dict, str]:
    if st.session_state.get("use_current_datetime", True):
        reference_iso = selected_datetime_iso()
        return reference_iso, reference_iso
    start_utc, end_utc = selected_analysis_window_utc()
    midpoint_utc = selected_analysis_midpoint_utc()
    reference_payload = {
        "start": start_utc.isoformat(),
        "end": end_utc.isoformat(),
        "reference": midpoint_utc.isoformat(),
    }
    return reference_payload, end_utc.isoformat()


def _detection_day_reference_payload() -> dict:
    selected_day = selected_datetime_local().date()
    start_local = datetime.combine(selected_day, time.min).replace(tzinfo=LOCAL_TZ)
    end_local = datetime.combine(selected_day, time.max).replace(tzinfo=LOCAL_TZ)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)
    midpoint_utc = start_utc + (end_utc - start_utc) / 2
    return {
        "start": start_utc.isoformat(),
        "end": end_utc.isoformat(),
        "reference": midpoint_utc.isoformat(),
    }


def update_day_detection_table(
    applied_indicators: List[str],
    roi,
    gdf,
    selected_companies,
    wind_context: dict,
) -> None:
    st.session_state["day_detection_rows"] = []
    st.session_state["day_detection_points"] = []
    st.session_state["day_detection_points_total"] = 0
    st.session_state["day_detection_status"] = ""
    st.session_state["day_detection_logs"] = []
    st.session_state["day_detection_period"] = ""
    if not applied_indicators or not roi:
        return

    day_reference = _detection_day_reference_payload()
    source_bundle = fetch_selected_sources(
        applied_indicators,
        roi,
        day_reference,
        active_fire_window_hours=None,
    )
    rows = compute_hotspot_distances(
        source_bundle["points"],
        gdf,
        selected_companies or [],
        limit=100000,
        max_distance_km=1_000_000.0,
        wind_context=wind_context,
        enforce_table_distance=False,
    )
    st.session_state["day_detection_rows"] = rows
    st.session_state["day_detection_points"] = source_bundle.get("points", [])
    st.session_state["day_detection_points_total"] = len(source_bundle.get("points", []))
    st.session_state["day_detection_logs"] = source_bundle.get("logs", [])
    st.session_state["day_detection_period"] = (
        f"{format_period_brasilia(day_reference['start'], day_reference['end'])} | "
        f"{format_period_zulu(day_reference['start'], day_reference['end'])}"
    )
    st.session_state["day_detection_status"] = (
        f"Consulta diaria concluida: {len(rows)} ponto(s) com fazenda mais proxima calculada "
        f"de {len(source_bundle.get('points', []))} deteccao(oes) amostradas."
    )


def apply_fire_risk_and_goes(
    selected: List[str],
    roi_result: dict | None = None,
    gdf=None,
    selected_companies=None,
    show_feedback: bool = True,
    record_applied_indicators: bool = True,
) -> None:
    roi = (roi_result or {}).get("geojson") or current_map_roi()
    roi_bounds = (roi_result or {}).get("bounds") or st.session_state.get("roi_bounds")
    applied_indicators = list(dict.fromkeys(selected))
    if record_applied_indicators:
        st.session_state["gee_applied_indicators"] = applied_indicators
    st.session_state["gee_roi"] = roi
    st.session_state["roi_ee"] = roi
    st.session_state["roi_bounds"] = roi_bounds
    st.session_state["applied_roi_bounds"] = roi_bounds
    st.session_state["gee_tile_layers"] = []
    st.session_state["fire_risk_layers"] = []
    st.session_state["last_goes_time"] = ""
    st.session_state["fire_risk_status"] = ""
    st.session_state["fire_detection_summary"] = {}
    st.session_state["analysis_reference_label"] = selected_analysis_label()
    st.session_state["analysis_image_rows"] = []
    if not applied_indicators:
        st.session_state["day_detection_rows"] = []
        st.session_state["day_detection_points"] = []
        st.session_state["day_detection_points_total"] = 0
        st.session_state["day_detection_status"] = ""
        st.session_state["day_detection_logs"] = []
        st.session_state["day_detection_period"] = ""
        if show_feedback:
            st.success("Nenhuma camada GE selecionada. Camadas removidas do mapa.")
        return
    if not roi:
        st.session_state["day_detection_rows"] = []
        st.session_state["day_detection_points"] = []
        st.session_state["day_detection_points_total"] = 0
        st.session_state["day_detection_status"] = ""
        st.session_state["day_detection_logs"] = []
        st.session_state["day_detection_period"] = ""
        if show_feedback:
            st.warning("Selecione uma empresa antes de aplicar a ROI.")
        return

    reference_query, risk_reference_iso = _analysis_reference_payload()
    active_fire_window_hours = CURRENT_ACTIVE_FIRE_WINDOW_HOURS if st.session_state.get("use_current_datetime", True) else None
    source_bundle = fetch_selected_sources(
        applied_indicators,
        roi,
        reference_query,
        active_fire_window_hours=active_fire_window_hours,
    )
    st.session_state["last_source_bundle_had_new_orbital_data"] = update_satellite_last_image_log(
        source_bundle.get("results", []),
        only_orbital=True,
    )
    source_layers = source_bundle["layers"]
    st.session_state["gee_tile_layers"] = source_layers
    status_messages = []
    if active_fire_window_hours is not None:
        status_messages.append("Deteccoes ativas consultadas nos ultimos 90 minutos por uso de data/hora atual.")
    else:
        status_messages.append("Deteccoes consultadas nas 24 horas do dia selecionado.")
    risk_panel = {"risk_value": None, "risk_class": "Nao calculado"}
    if RISK_INDICATOR in applied_indicators:
        result = build_fire_risk_index(roi, reference_datetime=risk_reference_iso)
        risk_panel = {
            "risk_value": result.get("risk_value"),
            "risk_class": result.get("risk_class", "Sem dados"),
        }
        status_messages.append(result.get("status", "Indice de risco processado."))

    wind_context = build_wind_context(roi_bounds)
    if wind_context.get("status"):
        status_messages.append(str(wind_context["status"]))
    nearest = compute_hotspot_distances(
        source_bundle["points"],
        gdf,
        selected_companies or [],
        limit=5000,
        max_distance_km=30.0,
        wind_context=wind_context,
    )
    all_roi_detections = compute_hotspot_distances(
        source_bundle["points"],
        gdf,
        selected_companies or [],
        limit=100000,
        max_distance_km=1_000_000.0,
        wind_context=wind_context,
        enforce_table_distance=False,
    )
    alert_rows = [row for row in nearest if row.get("alerta_sonoro")]
    alert_row = alert_rows[0] if alert_rows else None
    alert_level = classify_alert_level(nearest, risk_panel.get("risk_class", ""))
    st.session_state["fire_detection_summary"] = {
        **risk_panel,
        "counts": source_bundle.get("counts", {}),
        "nearest_farms": nearest,
        "all_roi_detections": all_roi_detections,
        "points": source_bundle["points"],
        "points_total": len(source_bundle["points"]),
        "status": alert_level,
        "fire_alert": bool(alert_row),
        "fire_alert_row": alert_row,
        "fire_alert_min_distance_km": alert_row.get("distancia_km") if alert_row else None,
        "fire_alert_threshold_km": alert_row.get("distancia_alerta_km") if alert_row else None,
        "wind_context": wind_context,
        "wind_alert_count": sum(1 for row in nearest if row.get("alerta_vento")),
    }
    update_day_detection_table(applied_indicators, roi, gdf, selected_companies, wind_context)

    st.session_state["fire_risk_layers"] = []
    st.session_state["last_goes_time"] = next(
        (
            f"{row.get('Data/hora Brasilia', '')} | {row.get('Data/hora Zulu', '')}"
            for row in source_bundle["image_rows"]
            if str(row.get("Camada", "")).startswith("GE | GOES")
        ),
        "",
    )
    st.session_state["source_results"] = source_bundle["results"]
    st.session_state["operational_log"] = source_bundle["logs"]
    st.session_state["analysis_image_rows"] = _analysis_image_rows(applied_indicators, source_bundle["image_rows"])
    plotted = sum(1 for log in source_bundle["logs"] if log.get("status") == "plotado")
    ignored = sum(1 for log in source_bundle["logs"] if log.get("status") != "plotado")
    st.session_state["fire_risk_status"] = (
        " ".join(message for message in status_messages if message)
        + f" Fontes orbitais processadas: {plotted} plotadas, {ignored} ignoradas."
    ).strip()
    if show_feedback:
        st.success(st.session_state["fire_risk_status"])


def maybe_refresh_layers() -> None:
    return


def maybe_auto_refresh_analysis(gdf) -> bool:
    if not st.session_state.get("use_current_datetime", True):
        return False
    if not st.session_state.get("auto_refresh_current_datetime"):
        return False
    if st.session_state.get("active_main_tab") == "Triangulacao":
        return False

    selected_companies = st.session_state.get("selected_companies", [])
    applied_indicators = st.session_state.get("gee_applied_indicators", [])
    if not selected_companies or not applied_indicators or not st.session_state.get("gee_roi"):
        return False

    now = auto_refresh_clock_now()
    last_value = st.session_state.get("last_auto_analysis_refresh")
    if last_value:
        try:
            elapsed = (now - datetime.fromisoformat(last_value)).total_seconds()
            if elapsed < AUTO_REFRESH_INTERVAL_SECONDS:
                return False
        except Exception:
            pass

    scheduled_indicators, schedule_status = scheduled_auto_refresh_indicators(applied_indicators, now)
    if not scheduled_indicators:
        finished_at = auto_refresh_clock_now()
        st.session_state["last_auto_analysis_refresh"] = finished_at.isoformat()
        st.session_state["last_auto_analysis_status"] = (
            f"{schedule_status} Nenhuma camada elegivel estava selecionada. "
            f"Proxima verificacao em {AUTO_REFRESH_INTERVAL_LABEL}."
        )
        st.session_state["auto_refresh_beep_pending"] = finished_at.isoformat()
        return True

    roi_result = build_project_roi(gdf, selected_companies)
    st.session_state["project_roi_result"] = roi_result
    st.session_state["roi_limit_status"] = roi_result["status"]
    if not roi_result["ok"]:
        return False
    st.session_state["viewport_fit_bounds"] = roi_result["bounds"]
    st.session_state["fit_viewport_on_next_map"] = True
    apply_fire_risk_and_goes(
        scheduled_indicators,
        roi_result=roi_result,
        gdf=gdf,
        selected_companies=selected_companies,
        show_feedback=False,
        record_applied_indicators=False,
    )
    apply_manual_coordinate_analysis(gdf, selected_companies, show_feedback=False)
    finished_at = auto_refresh_clock_now()
    st.session_state["last_auto_analysis_refresh"] = finished_at.isoformat()
    st.session_state["auto_refresh_full_satellite_query_done"] = True
    if st.session_state.get("pending_orbital_checkpoint"):
        has_orbital_data = orbital_sources_found(st.session_state.get("source_results", []))
        has_new_orbital_data = bool(st.session_state.get("last_source_bundle_had_new_orbital_data"))
        if has_orbital_data and has_new_orbital_data:
            st.session_state["last_orbital_image_found_at"] = finished_at.isoformat()
            st.session_state["last_orbital_image_checkpoint"] = st.session_state.get("pending_orbital_checkpoint")
            st.session_state.pop("pending_orbital_checkpoint", None)
            schedule_status += " Imagem orbital encontrada; proxima tentativa ficara para o proximo horario programado."
        elif has_orbital_data:
            schedule_status += " A consulta retornou dado orbital ja registrado; a tentativa continuara ate aparecer imagem nova."
        else:
            schedule_status += " Nenhuma imagem orbital nova foi encontrada; a tentativa continuara no proximo ciclo de 15 minutos."
    st.session_state["last_auto_analysis_status"] = (
        f"Atualizacao automatica concluida em {format_datetime_brasilia(finished_at)}. {schedule_status}"
    )
    st.session_state["auto_refresh_beep_pending"] = finished_at.isoformat()
    return True


def render_gee_tab(gdf) -> None:
    st.markdown("### GE - risco e focos de incendio")
    catalog = load_gee_catalog()
    st.session_state["gee_catalog"] = catalog
    if not catalog["ok"]:
        st.info(catalog["message"])

    selected = []
    if st.session_state.get("gee_defaults_version") != 5:
        st.session_state["gee_indicators"] = DEFAULT_GEE_INDICATORS.copy()
        st.session_state["gee_defaults_version"] = 5
    current = set(st.session_state.get("gee_indicators", DEFAULT_GEE_INDICATORS))
    st.caption("Marque os dados GE que devem compor as camadas operacionais.")
    for name in SATELLITE_OPTIONS:
        if st.checkbox(name, value=name in current, key=f"gee_{name}"):
            selected.append(name)
        st.caption(SATELLITE_DESCRIPTIONS.get(name, SATELLITE_OPTIONS[name]))
    st.session_state["gee_indicators"] = selected

    st.caption("O Aplicar calcula uma ROI unica a partir das empresas selecionadas, com buffer de 30 km.")

    if st.session_state.get("gee_roi"):
        st.caption("ROI atual: envelope das empresas selecionadas com buffer de 30 km.")
    if st.session_state.get("last_goes_time"):
        st.caption(f"Ultima imagem GOES: {st.session_state['last_goes_time']}")
    if st.session_state.get("fire_risk_status"):
        st.caption(st.session_state["fire_risk_status"])
    if st.session_state.get("roi_limit_status"):
        st.caption(st.session_state["roi_limit_status"])


def render_coordinates_tab(gdf) -> None:
    st.markdown("### Coordenadas")
    st.caption(
        "Digite um ponto para plotar no mapa e calcular a distancia ate a fazenda mais proxima. "
        "O formato padrao e graus decimais."
    )

    current_mode = st.session_state.get("manual_coordinate_mode", "decimal")
    mode_cols = st.columns(3)
    decimal_checked = mode_cols[0].checkbox("Graus decimais", value=current_mode == "decimal", key="manual_coordinate_mode_decimal")
    dms_checked = mode_cols[1].checkbox("Graus, minutos e segundos", value=current_mode == "dms", key="manual_coordinate_mode_dms")
    utm_checked = mode_cols[2].checkbox("UTM", value=current_mode == "utm", key="manual_coordinate_mode_utm")
    if utm_checked:
        st.session_state["manual_coordinate_mode"] = "utm"
    elif dms_checked:
        st.session_state["manual_coordinate_mode"] = "dms"
    else:
        st.session_state["manual_coordinate_mode"] = "decimal"
    if sum([bool(decimal_checked), bool(dms_checked), bool(utm_checked)]) > 1:
        st.caption("Se mais de um formato estiver marcado, sera usado o formato mais a direita: UTM, depois GMS, depois decimal.")

    if st.session_state["manual_coordinate_mode"] == "decimal":
        coord_cols = st.columns(2)
        with coord_cols[0]:
            st.text_input("Latitude decimal", key="manual_coordinate_lat", placeholder="-20.123456")
        with coord_cols[1]:
            st.text_input("Longitude decimal", key="manual_coordinate_lon", placeholder="-54.123456")
    elif st.session_state["manual_coordinate_mode"] == "dms":
        coord_cols = st.columns(2)
        with coord_cols[0]:
            st.text_input("Latitude GMS", key="manual_coordinate_dms_lat", placeholder='20° 12\' 34.5" S')
        with coord_cols[1]:
            st.text_input("Longitude GMS", key="manual_coordinate_dms_lon", placeholder='54° 12\' 34.5" W')
    else:
        st.checkbox(
            "Usar SIRGAS 2000 / UTM Sul",
            value=st.session_state.get("manual_coordinate_utm_sirgas", True),
            key="manual_coordinate_utm_sirgas",
            help="Padrao do sistema. Para zona 22S, corresponde ao EPSG:31982.",
        )
        utm_cols = st.columns([0.38, 0.38, 0.24])
        with utm_cols[0]:
            st.text_input("Leste / Easting", key="manual_coordinate_utm_easting", placeholder="750000")
        with utm_cols[1]:
            st.text_input("Norte / Northing", key="manual_coordinate_utm_northing", placeholder="7800000")
        with utm_cols[2]:
            st.number_input("Zona S", min_value=18, max_value=25, value=int(st.session_state.get("manual_coordinate_utm_zone", 22) or 22), step=1, key="manual_coordinate_utm_zone")
        st.caption("Padrao: SIRGAS 2000 / UTM 22S (EPSG:31982).")

    st.number_input(
        "Raio do círculo (m)",
        min_value=1,
        max_value=100000,
        value=int(st.session_state.get("manual_coordinate_buffer_m", 5000) or 5000),
        step=100,
        key="manual_coordinate_buffer_m",
        help="Gera um círculo no mapa a partir da coordenada digitada. O padrão é 5000 metros.",
    )

    manual_result = st.session_state.get("manual_coordinate_distance")
    if manual_result:
        st.caption(
            f"Ponto aplicado: fazenda mais proxima {manual_result.get('fazenda', '-')}, "
            f"{manual_result.get('distancia_km', '-')} km, vento para fazenda: "
            f"{manual_result.get('vento_para_fazenda', 'Sem dados')}."
        )
    if st.button("Aplicar coordenada", type="primary", use_container_width=True, key="apply_coordinate"):
        apply_manual_coordinate_analysis(
            gdf,
            st.session_state.get("selected_companies", []),
            show_feedback=True,
        )
        st.session_state["active_main_tab"] = "Mapa Operacional"
        st.rerun()


def render_apply_controls(gdf, pending_companies: List[str]) -> None:
    pending_companies = list(dict.fromkeys(pending_companies or []))
    current_indicators = list(st.session_state.get("gee_indicators", DEFAULT_GEE_INDICATORS))
    applied_companies = list(st.session_state.get("applied_company_selection", st.session_state.get("selected_companies", [])))
    applied_indicators = list(st.session_state.get("gee_applied_indicators", []))
    has_pending_changes = pending_companies != applied_companies or current_indicators != applied_indicators
    if has_pending_changes:
        st.caption("Ha alteracoes pendentes. Clique em Aplicar consulta para recalcular empresas, ROI, risco e deteccoes.")
    label = "Aplicar consulta" if has_pending_changes else "Aplicar consulta"
    if st.button(label, type="primary", use_container_width=True, key="apply_query"):
        apply_sidebar_selection(gdf, pending_companies, current_indicators)
        st.rerun()

def ensure_points_state() -> None:
    existing_points = st.session_state.get("triangulation_points", [])
    if "triangulation_rows" not in st.session_state:
        if existing_points:
            st.session_state["triangulation_rows"] = [
                {
                    "lon": f"{float(point['lon']):.6f}",
                    "lat": f"{float(point['lat']):.6f}",
                    "angle": float(point.get("angle", 0.0)) % 360,
                }
                for point in existing_points
            ]
        else:
            st.session_state["triangulation_rows"] = [{"lon": "", "lat": "", "angle": 0.0}]
    st.session_state.setdefault("triangulation_points", [])
    if not st.session_state["triangulation_rows"]:
        st.session_state["triangulation_rows"] = [{"lon": "", "lat": "", "angle": 0.0}]


def sync_triangulation_widgets_from_rows() -> None:
    force = bool(st.session_state.pop("triangulation_force_widget_sync", False))
    for idx, row in enumerate(st.session_state.get("triangulation_rows", [])):
        lon_key = f"tower_lon_{idx}"
        lat_key = f"tower_lat_{idx}"
        angle_key = f"tower_angle_row_{idx}"
        if force or lon_key not in st.session_state:
            st.session_state[lon_key] = str(row.get("lon", ""))
        if force or lat_key not in st.session_state:
            st.session_state[lat_key] = str(row.get("lat", ""))
        if force or angle_key not in st.session_state:
            st.session_state[angle_key] = float(row.get("angle", 0.0) or 0.0)
        dial_key = f"tower_angle_dial_{idx}"
        if force or dial_key not in st.session_state:
            st.session_state[dial_key] = int(round(float(row.get("angle", 0.0) or 0.0))) % 361


def _row_to_point(row: dict) -> dict | None:
    lon_value = str(row.get("lon", "")).strip()
    lat_value = str(row.get("lat", "")).strip()
    if not lon_value and not lat_value:
        return None
    lon = float(lon_value.replace(",", "."))
    lat = float(lat_value.replace(",", "."))
    angle = float(row.get("angle") or 0.0) % 360
    if not -180 <= lon <= 180:
        raise ValueError("Longitude deve estar entre -180 e 180.")
    if not -90 <= lat <= 90:
        raise ValueError("Latitude deve estar entre -90 e 90.")
    return {"lon": lon, "lat": lat, "angle": angle}


def sync_triangulation_points(validate: bool = False) -> bool:
    points = []
    try:
        for row in st.session_state.get("triangulation_rows", []):
            point = _row_to_point(row)
            if point:
                points.append(point)
            elif validate and (str(row.get("lon", "")).strip() or str(row.get("lat", "")).strip()):
                raise ValueError("Preencha longitude e latitude do ponto ou deixe a linha vazia.")
    except Exception as exc:
        if validate:
            st.error(f"Nao foi possivel aplicar a triangulacao: {exc}")
        return False
    st.session_state["triangulation_points"] = points
    st.session_state["rotate_point_index"] = min(
        int(st.session_state.get("rotate_point_index", 0) or 0),
        max(len(points) - 1, 0),
    )
    return True


def add_tower_row(values: dict | None = None) -> None:
    values = values or {}
    st.session_state["triangulation_rows"].append(
        {
            "lon": values.get("lon", ""),
            "lat": values.get("lat", ""),
            "angle": float(values.get("angle", 0.0)) % 360,
        }
    )
    st.session_state["triangulation_force_widget_sync"] = True


def fill_next_empty_tower(lat: float, lon: float) -> None:
    rows = st.session_state["triangulation_rows"]
    target = next((row for row in rows if not str(row.get("lon", "")).strip() or not str(row.get("lat", "")).strip()), None)
    if target is None:
        target = {"lon": "", "lat": "", "angle": 0.0}
        rows.append(target)
    target["lon"] = f"{float(lon):.6f}"
    target["lat"] = f"{float(lat):.6f}"
    target["angle"] = 0.0
    st.session_state["triangulation_force_widget_sync"] = True
    sync_triangulation_points(validate=False)


@st.dialog("Usar esta coordenada?", width="small")
def coordinate_accept_dialog() -> None:
    pending = st.session_state.get("pending_tower_click")
    if not pending:
        st.rerun()
    st.markdown(f"**Longitude:** `{pending['lon']:.6f}`")
    st.markdown(f"**Latitude:** `{pending['lat']:.6f}`")
    cols = st.columns(2)
    with cols[0]:
        if st.button("Aceitar", type="primary", use_container_width=True):
            fill_next_empty_tower(pending["lat"], pending["lon"])
            st.session_state["pending_tower_click"] = None
            st.rerun()
    with cols[1]:
        if st.button("Cancelar", use_container_width=True):
            st.session_state["pending_tower_click"] = None
            st.rerun()


def clear_or_delete_tower_row(index: int) -> None:
    rows = st.session_state["triangulation_rows"]
    if len(rows) <= 1:
        rows[0] = {"lon": "", "lat": "", "angle": 0.0}
        st.session_state["triangulation_points"] = []
        st.session_state["rotate_point_mode"] = False
        st.session_state["select_line_mode"] = False
        st.session_state["pending_tower_click"] = None
        st.session_state["last_map_click"] = None
        st.session_state["triangulation_force_widget_sync"] = True
        return
    if 0 <= index < len(rows):
        del rows[index]
    sync_triangulation_points(validate=False)
    st.session_state["last_map_click"] = None
    st.session_state["triangulation_force_widget_sync"] = True


def render_pending_map_coordinate() -> None:
    pending = st.session_state.get("pending_tower_click")
    if not pending:
        return
    coordinate_accept_dialog()


def render_triangulation_controls() -> float:
    st.markdown("### Triangulacao")
    ensure_points_state()
    sync_triangulation_widgets_from_rows()
    st.caption("Pressione Ctrl para capturar uma coordenada. Pressione Shift para rotacionar a linha desejada.")

    cols_header = st.columns([0.55, 0.45])
    with cols_header[0]:
        st.markdown("#### Pontos de observacao")
    with cols_header[1]:
        if st.button("Adicionar ponto", use_container_width=True):
            add_tower_row()
            st.rerun()

    range_km = st.number_input("Alcance padrao (km)", min_value=0.1, value=DEFAULT_RANGE_KM, step=0.5)
    st.session_state["range_km"] = float(range_km)

    for idx, row in enumerate(st.session_state["triangulation_rows"]):
        st.markdown(f"**Ponto {idx + 1}**")
        cols = st.columns([0.28, 0.28, 0.30, 0.14])
        row["lon"] = cols[0].text_input("Longitude", value=str(row.get("lon", "")), key=f"tower_lon_{idx}")
        row["lat"] = cols[1].text_input("Latitude", value=str(row.get("lat", "")), key=f"tower_lat_{idx}")
        row["angle"] = float(cols[2].slider(
            "Angulo norte / girar",
            min_value=0,
            max_value=360,
            value=int(round(float(row.get("angle", 0.0) or 0.0))) % 361,
            step=1,
            key=f"tower_angle_dial_{idx}",
            help="Gire este controle para rotacionar a linha no mapa.",
        ))
        cols[2].caption(f"{row['angle']:.0f} graus")
        st.session_state[f"tower_angle_row_{idx}"] = row["angle"]
        cols[2].markdown(
            f"""
            <div style="
                width:56px;height:56px;border-radius:999px;border:2px solid #ffcf4a;
                display:flex;align-items:center;justify-content:center;margin-top:-8px;
                background:rgba(15,23,42,.55);">
                <div style="
                    width:2px;height:24px;background:#ffcf4a;transform-origin:50% 100%;
                    transform:rotate({row['angle']}deg);border-radius:99px;"></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if cols[3].button("X", key=f"delete_tower_row_{idx}", help="Excluir coordenada", use_container_width=True):
            clear_or_delete_tower_row(idx)
            st.rerun()

    cols_apply = st.columns(2)
    with cols_apply[0]:
        if st.button("Aplicar", type="primary", use_container_width=True):
            if sync_triangulation_points(validate=True):
                st.success("Triangulacao aplicada.")
                st.rerun()
    with cols_apply[1]:
        if st.button("Limpar torres", use_container_width=True):
            st.session_state["triangulation_rows"] = [{"lon": "", "lat": "", "angle": 0.0}]
            st.session_state["triangulation_points"] = []
            st.session_state["pending_tower_click"] = None
            st.session_state["last_map_click"] = None
            st.session_state["rotate_point_mode"] = False
            st.session_state["select_line_mode"] = False
            st.session_state["triangulation_force_widget_sync"] = True
            st.rerun()

    sync_triangulation_points(validate=False)
    st.caption("Ctrl + clique captura uma coordenada. Shift + clique seleciona/rotaciona uma linha.")
    render_pending_map_coordinate()

    point_labels = [
        f"Ponto {idx + 1}: {point['angle']:.2f} graus / {range_km:.2f} km"
        for idx, point in enumerate(st.session_state["triangulation_points"])
    ]
    if point_labels:
        st.session_state["rotate_point_index"] = st.selectbox(
            "Linha para rotacionar",
            range(len(point_labels)),
            format_func=lambda index: point_labels[index],
            index=min(int(st.session_state.get("rotate_point_index", 0) or 0), len(point_labels) - 1),
        )
        rot_cols = st.columns(2)
        with rot_cols[0]:
            if st.button("Selecionar linha pelo clique", use_container_width=True):
                st.session_state["select_line_mode"] = True
                st.session_state["rotate_point_mode"] = False
                st.rerun()
        with rot_cols[1]:
            if st.button("Rotacionar por clique", use_container_width=True):
                st.session_state["rotate_point_mode"] = True
                st.session_state["select_line_mode"] = False
                st.rerun()
        if st.session_state.get("select_line_mode"):
            st.info("Clique proximo de uma linha no mapa para seleciona-la.")
        if st.session_state.get("rotate_point_mode"):
            st.info("Clique na direcao desejada para atualizar o angulo da linha selecionada.")
    else:
        st.caption("Nenhum ponto aplicado. Digite longitude/latitude ou capture uma coordenada no mapa.")

    if st.session_state.get("intersection_count", 0):
        st.metric("Cruzamentos", st.session_state["intersection_count"])
    return range_km


def render_company_tab() -> None:
    st.markdown("### Cadastro de Empresas")
    st.info("Area reservada para cadastro e manutencao dos dados das empresas na proxima versao.")


def render_sidebar(gdf) -> Tuple[List[str], float]:
    with st.sidebar:
        st.markdown("## Empresa / GE")
        with st.expander("Data e hora", expanded=True):
            render_datetime_tab()
        with st.expander("Empresa", expanded=True):
            pending_companies = render_project_tab(gdf)
        with st.expander("GE", expanded=False):
            render_gee_tab(gdf)
        render_apply_controls(gdf, pending_companies)
        with st.expander("Coordenadas", expanded=False):
            render_coordinates_tab(gdf)

    selected_companies = st.session_state.get("selected_companies", [])
    range_km = float(st.session_state.get("range_km", DEFAULT_RANGE_KM))
    return selected_companies, range_km

