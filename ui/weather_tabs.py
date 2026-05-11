# -*- coding: utf-8 -*-
from __future__ import annotations

from io import BytesIO
from datetime import date, datetime, timedelta
from typing import Dict, Optional, Tuple

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from shapely.geometry import shape

from core.time_context import selected_date
from services.weather_service import (
    fetch_weather_window,
    weather_code_label,
)

FORECAST_DAYS = 16
FORECAST_CARD_DAYS = 3


def _roi_center_from_bounds(bounds) -> Optional[Tuple[float, float]]:
    try:
        south, west = bounds[0]
        north, east = bounds[1]
        return (float(south) + float(north)) / 2.0, (float(west) + float(east)) / 2.0
    except Exception:
        return None


def _roi_center_from_geojson(geojson: Dict | None) -> Optional[Tuple[float, float]]:
    if not geojson:
        return None
    try:
        centroid = shape(geojson).centroid
        return float(centroid.y), float(centroid.x)
    except Exception:
        return None


def current_roi_center() -> Optional[Tuple[float, float]]:
    return (
        _roi_center_from_bounds(st.session_state.get("applied_roi_bounds"))
        or _roi_center_from_bounds(st.session_state.get("roi_bounds"))
        or _roi_center_from_geojson(st.session_state.get("roi_ee"))
        or _roi_center_from_geojson(st.session_state.get("gee_roi"))
    )


@st.cache_data(ttl=900, show_spinner=False)
def _cached_forecast(lat: float, lon: float, reference_day_iso: str) -> Dict:
    return fetch_weather_window(lat, lon, date.fromisoformat(reference_day_iso), days=FORECAST_DAYS)


def _daily_dataframe(data: Dict) -> pd.DataFrame:
    daily = data.get("daily") or {}
    df = pd.DataFrame(daily)
    if df.empty:
        return df
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"])
    if "weather_code" in df.columns:
        df["condicao"] = df["weather_code"].map(weather_code_label)
    return df


def _hourly_dataframe(data: Dict) -> pd.DataFrame:
    hourly = data.get("hourly") or {}
    df = pd.DataFrame(hourly)
    if df.empty:
        return df
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"])
    return df


def _metric_value(value, suffix: str = "", decimals: int = 1) -> str:
    if value is None or pd.isna(value):
        return "-"
    try:
        return f"{float(value):.{decimals}f}{suffix}"
    except Exception:
        return str(value)


def _excel_bytes(df: pd.DataFrame, sheet_name: str) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name[:31])
    return output.getvalue()


def _daily_humidity(hourly_df: pd.DataFrame) -> pd.DataFrame:
    if hourly_df.empty or "relative_humidity_2m" not in hourly_df.columns or "time" not in hourly_df.columns:
        return pd.DataFrame(columns=["date", "umidade_media"])
    grouped = hourly_df.copy()
    grouped["date"] = grouped["time"].dt.date
    return (
        grouped.groupby("date", as_index=False)["relative_humidity_2m"]
        .mean()
        .rename(columns={"relative_humidity_2m": "umidade_media"})
    )


def _prepare_daily_summary(daily_df: pd.DataFrame, hourly_df: pd.DataFrame) -> pd.DataFrame:
    if daily_df.empty:
        return daily_df
    summary = daily_df.copy()
    summary["date"] = summary["time"].dt.date
    humidity = _daily_humidity(hourly_df)
    if not humidity.empty:
        summary = summary.merge(humidity, on="date", how="left")
    if "precipitation_sum" in summary.columns:
        summary["precipitation_accumulated"] = summary["precipitation_sum"].fillna(0).cumsum()
    return summary


def _render_weather_location(lat: float, lon: float) -> None:
    st.caption(
        "Ponto de previsao: centro da ROI aplicada "
        f"({lat:.6f}, {lon:.6f})."
    )


def _selected_area_label(prefix: str) -> str:
    companies = st.session_state.get("selected_companies", []) or []
    if companies:
        joined = ", ".join(str(company) for company in companies[:4])
        suffix = "" if len(companies) <= 4 else " ..."
        return f"{prefix} {joined}{suffix}"
    return f"{prefix} area selecionada"


def _classify_region(lat: float, lon: float) -> str:
    if lat < -23:
        return "Sul"
    if lat < -15:
        return "Sudeste"
    if lat < -8:
        return "Centro-Oeste"
    if lat < -2:
        return "Nordeste"
    return "Norte"


def _month_window(reference_day: date, months: int) -> str:
    names = {
        1: "jan",
        2: "fev",
        3: "mar",
        4: "abr",
        5: "mai",
        6: "jun",
        7: "jul",
        8: "ago",
        9: "set",
        10: "out",
        11: "nov",
        12: "dez",
    }
    start_month = reference_day.month
    start_year = reference_day.year
    end_month = start_month + max(int(months), 1) - 1
    end_year = start_year
    while end_month > 12:
        end_month -= 12
        end_year += 1
    return f"{names[start_month]}/{start_year} a {names[end_month]}/{end_year}"


def _generated_at_label() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M")


def _trend_text_3_months(local_name: str, region: str) -> str:
    if region == "Sul":
        return (
            f"Para {local_name}, os proximos 3 meses sugerem alternancia entre momentos de melhor umidade "
            "e intervalos de maior restricao hidrica. Esse comportamento pede flexibilidade nas operacoes "
            "e acompanhamento frequente da distribuicao das chuvas, nao apenas do volume acumulado."
        )
    if region == "Sudeste":
        return (
            f"Para {local_name}, a tendencia de 3 meses aponta para reducao gradual das chuvas em parte "
            "do periodo, com possibilidade de temperaturas mais elevadas. Isso pode aumentar a perda de "
            "umidade do solo e elevar a sensibilidade operacional em areas mais secas."
        )
    if region == "Centro-Oeste":
        return (
            f"Para {local_name}, os proximos 3 meses tendem a manter um padrao mais seco, com menor "
            "frequencia de chuva e maior potencial de restricao hidrica entre eventos de precipitacao. "
            "A leitura operacional recomenda vigilancia reforcada sobre balanco hidrico, combustivel seco "
            "e janelas de campo."
        )
    if region == "Nordeste":
        return (
            f"Para {local_name}, o cenario de 3 meses indica persistencia de irregularidade na distribuicao "
            "das chuvas. Mesmo quando houver precipitacao, ela pode ser insuficiente para sustentar umidade "
            "regular ao longo do periodo."
        )
    return (
        f"Para {local_name}, os proximos 3 meses tendem a manter temperaturas elevadas, com chuva em parte "
        "do periodo e distribuicao potencialmente irregular. O acompanhamento deve observar a sequencia dos "
        "eventos de chuva e a resposta da umidade do solo."
    )


def _trend_text_6_months(local_name: str, region: str) -> str:
    if region == "Sul":
        return (
            f"Para {local_name}, a leitura de 6 meses sugere continuidade de alta variabilidade climatica, "
            "com alternancia entre fases mais umidas e periodos de maior restricao hidrica. O planejamento "
            "deve trabalhar com cenarios e revisar prioridades conforme a evolucao real da chuva."
        )
    if region == "Sudeste":
        return (
            f"Para {local_name}, o horizonte de 6 meses sugere consolidacao de uma fase mais seca em parte "
            "do ciclo, seguida por transicao gradual para condicoes menos restritivas. Se as temperaturas "
            "permanecerem elevadas, o deficit hidrico pode se intensificar em momentos especificos."
        )
    if region == "Centro-Oeste":
        return (
            f"Para {local_name}, a tendencia de 6 meses indica sazonalidade bem marcada, com fase seca mais "
            "definida antes da retomada gradual das chuvas. Essa leitura ajuda a antecipar monitoramento, "
            "organizacao de recursos e prioridades de prevencao."
        )
    if region == "Nordeste":
        return (
            f"Para {local_name}, o horizonte de 6 meses aponta para continuidade da irregularidade das chuvas, "
            "com chance de intervalos secos mais prolongados. O ponto critico e a regularidade da umidade ao "
            "longo do tempo, mais do que apenas o total acumulado de precipitacao."
        )
    return (
        f"Para {local_name}, a leitura de 6 meses sugere persistencia de temperaturas elevadas e comportamento "
        "variavel da precipitacao. A recomendacao e usar essa tendencia como orientacao de planejamento e "
        "refina-la com a previsao de curto prazo e observacao local."
    )


def _render_trend_block(title: str, text: str, reference: str, generated_at: str) -> None:
    st.markdown(
        f"""
        <div style="
            background: rgba(16, 185, 129, 0.10);
            border-left: 6px solid #22c55e;
            border-radius: 12px;
            padding: 18px 20px;
            margin: 8px 0 16px 0;
            border-top: 1px solid rgba(52, 211, 153, 0.16);
            border-right: 1px solid rgba(52, 211, 153, 0.16);
            border-bottom: 1px solid rgba(52, 211, 153, 0.16);
        ">
            <div style="font-size:1.10rem;font-weight:800;color:#dcfce7;margin-bottom:10px;">{title}</div>
            <div style="font-size:1rem;line-height:1.75;color:#e5e7eb;text-align:justify;">{text}</div>
            <div style="
                margin-top:12px;
                padding-top:10px;
                border-top:1px solid rgba(187,247,208,0.20);
                font-size:0.90rem;
                line-height:1.55;
                color:#bbf7d0;
            ">
                <strong>Fonte:</strong> Interpretacao climatica regional baseada no centro da ROI aplicada.<br>
                <strong>Referencia:</strong> Janela sazonal estimada: {reference}<br>
                <strong>Gerado em:</strong> {generated_at}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_weather_forecast_tab() -> None:
    st.subheader("Previsao do tempo")
    center = current_roi_center()
    if not center:
        st.info("Aplique uma empresa/ROI no menu lateral para calcular a previsao no centro da ROI.")
        return

    lat, lon = center
    _render_weather_location(lat, lon)
    reference_day = selected_date()
    end_day = reference_day + timedelta(days=FORECAST_DAYS - 1)
    st.caption(
        "Janela da previsao: "
        f"{reference_day.strftime('%d/%m/%Y')} a {end_day.strftime('%d/%m/%Y')}."
    )
    with st.expander("Fonte dos dados", expanded=False):
        st.markdown(
            f"""
**Origem:** Open-Meteo  
**Tipo de consulta:** API por coordenadas geograficas  
**Area consultada:** centro da ROI aplicada  
**Latitude:** `{lat:.6f}`  
**Longitude:** `{lon:.6f}`  

**Variaveis consultadas:**  
- codigo meteorologico WMO
- temperatura maxima e minima diaria
- precipitacao diaria e acumulada
- probabilidade maxima diaria de chuva
- vento e rajadas maximas
- umidade relativa horaria consolidada por dia

**Forma de exibicao:**  
O painel superior mostra apenas a data selecionada, amanha e depois de amanha.
Os graficos e a tabela usam o horizonte completo de {FORECAST_DAYS} dias.
"""
        )
    show_table = st.checkbox("Exibir tabela completa", value=True, key="weather_show_full_table")
    if st.button("Atualizar previsao", use_container_width=False):
        _cached_forecast.clear()

    try:
        with st.spinner("Carregando previsao meteorologica..."):
            data = _cached_forecast(round(lat, 6), round(lon, 6), reference_day.isoformat())
    except Exception as exc:
        st.error(f"Nao foi possivel carregar a previsao do tempo: {exc}")
        return

    hourly_df = _hourly_dataframe(data)
    daily_df = _daily_dataframe(data)
    daily_summary = _prepare_daily_summary(daily_df, hourly_df)

    if not daily_summary.empty:
        st.markdown("#### Previsao para a data selecionada, amanha e depois de amanha")
        day_rows = daily_summary.head(FORECAST_CARD_DAYS).reset_index(drop=True)
        day_labels = ["Data selecionada", "Amanha", "Depois de amanha"]
        day_cols = st.columns(max(len(day_rows), 1))
        for idx, col in enumerate(day_cols):
            row = day_rows.iloc[idx]
            label = day_labels[idx] if idx < len(day_labels) else f"Dia +{idx}"
            with col:
                st.markdown(f"**{label}**")
                st.caption(pd.to_datetime(row["time"]).strftime("%d/%m/%Y"))
                st.metric(
                    "Temp. min/max",
                    f"{_metric_value(row.get('temperature_2m_min'), ' C')} / "
                    f"{_metric_value(row.get('temperature_2m_max'), ' C')}",
                )
                st.metric("Umidade media", _metric_value(row.get("umidade_media"), "%", 0))
                st.metric("Chuva prevista", _metric_value(row.get("precipitation_sum"), " mm"))

        st.markdown(f"#### Graficos de tendencia para os proximos {FORECAST_DAYS} dias")

    if not daily_summary.empty:
        chart_a, chart_b = st.columns(2)
        with chart_a:
            fig_daily_temp = go.Figure()
            fig_daily_temp.add_trace(
                go.Scatter(
                    x=daily_summary["time"],
                    y=daily_summary.get("temperature_2m_max", pd.Series(dtype=float)),
                    name="Temp. max (C)",
                    mode="lines+markers",
                )
            )
            fig_daily_temp.add_trace(
                go.Scatter(
                    x=daily_summary["time"],
                    y=daily_summary.get("temperature_2m_min", pd.Series(dtype=float)),
                    name="Temp. min (C)",
                    mode="lines+markers",
                )
            )
            fig_daily_temp.update_layout(
                height=310,
                margin=dict(l=10, r=10, t=35, b=10),
                title="Temperatura diaria - 16 dias",
            )
            st.plotly_chart(fig_daily_temp, use_container_width=True)

        with chart_b:
            fig_daily_wind = go.Figure()
            fig_daily_wind.add_trace(
                go.Scatter(
                    x=daily_summary["time"],
                    y=daily_summary.get("wind_speed_10m_max", pd.Series(dtype=float)),
                    name="Vento max (km/h)",
                    mode="lines+markers",
                )
            )
            fig_daily_wind.add_trace(
                go.Scatter(
                    x=daily_summary["time"],
                    y=daily_summary.get("wind_gusts_10m_max", pd.Series(dtype=float)),
                    name="Rajada max (km/h)",
                    mode="lines+markers",
                )
            )
            fig_daily_wind.update_layout(
                height=310,
                margin=dict(l=10, r=10, t=35, b=10),
                title="Vento e rajadas - 16 dias",
            )
            st.plotly_chart(fig_daily_wind, use_container_width=True)

        fig_daily_rain = go.Figure()
        fig_daily_rain.add_trace(
            go.Bar(
                x=daily_summary["time"],
                y=daily_summary.get("precipitation_sum", pd.Series(dtype=float)),
                name="Precipitacao diaria (mm)",
            )
        )
        fig_daily_rain.add_trace(
            go.Scatter(
                x=daily_summary["time"],
                y=daily_summary.get("precipitation_accumulated", pd.Series(dtype=float)),
                name="Precipitacao acumulada (mm)",
                mode="lines+markers",
            )
        )
        fig_daily_rain.update_layout(height=310, margin=dict(l=10, r=10, t=35, b=10), title="Precipitacao diaria e acumulada")
        st.plotly_chart(fig_daily_rain, use_container_width=True)

        if "precipitation_probability_max" in daily_summary.columns:
            fig_daily_prob = go.Figure()
            fig_daily_prob.add_trace(
                go.Bar(
                    x=daily_summary["time"],
                    y=daily_summary["precipitation_probability_max"],
                    name="Prob. chuva (%)",
                )
            )
            fig_daily_prob.update_layout(
                height=280,
                margin=dict(l=10, r=10, t=35, b=10),
                title="Probabilidade maxima diaria de chuva - 16 dias",
            )
            st.plotly_chart(fig_daily_prob, use_container_width=True)
        elif hourly_df.empty:
            st.info("Probabilidade de precipitacao nao disponivel para dados historicos.")

        columns = [
            "time",
            "condicao",
            "umidade_media",
            "temperature_2m_min",
            "temperature_2m_max",
            "precipitation_sum",
            "precipitation_accumulated",
            "precipitation_probability_max",
            "wind_speed_10m_max",
            "wind_gusts_10m_max",
            "uv_index_max",
        ]
        labels = {
            "time": "Data",
            "condicao": "Condicao",
            "umidade_media": "Umidade media (%)",
            "temperature_2m_min": "Temp. min (C)",
            "temperature_2m_max": "Temp. max (C)",
            "precipitation_sum": "Chuva (mm)",
            "precipitation_accumulated": "Chuva acumulada (mm)",
            "precipitation_probability_max": "Prob. chuva (%)",
            "wind_speed_10m_max": "Vento max (km/h)",
            "wind_gusts_10m_max": "Rajada max (km/h)",
            "uv_index_max": "UV max",
        }
        visible = [column for column in columns if column in daily_summary.columns]
        export_df = daily_summary[visible].rename(columns=labels)
        st.download_button(
            "Exportar resumo diario para Excel",
            data=_excel_bytes(export_df, "Resumo Diario"),
            file_name=f"previsao_tempo_{reference_day.isoformat()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=False,
        )
        if show_table:
            st.markdown(f"#### Resumo diario dos {FORECAST_DAYS} dias")
            st.dataframe(export_df, use_container_width=True, hide_index=True)


def render_climate_trend_tab() -> None:
    st.subheader("Tendencia climatica")
    center = current_roi_center()
    if not center:
        st.info("Aplique uma empresa/ROI no menu lateral para calcular a tendencia no centro da ROI.")
        return

    lat, lon = center
    _render_weather_location(lat, lon)
    reference_day = selected_date()
    region = _classify_region(lat, lon)
    local_name = _selected_area_label("as empresas selecionadas:")
    generated_at = _generated_at_label()

    st.markdown(f"### {_selected_area_label('Tendencia climatica para')}")
    st.caption(
        "Leitura sazonal interpretativa baseada no centro da ROI aplicada. "
        f"Data de referencia: {reference_day.strftime('%d/%m/%Y')}."
    )

    with st.expander("Fonte dos dados", expanded=False):
        st.markdown(
            f"""
**Tipo de informacao exibida:** tendencia climatica sazonal interpretativa  
**Area consultada:** centro da ROI aplicada  
**Latitude:** `{lat:.6f}`  
**Longitude:** `{lon:.6f}`  
**Regiao climatica usada:** `{region}`  

**Estrutura atual da aba:**  
Esta leitura segue o modelo da aba de tendencia climatica do projeto ClimaV22,
com dois horizontes operacionais: proximos 3 meses e proximos 6 meses.

**Observacao importante:**  
A tendencia climatica deve apoiar planejamento e priorizacao operacional.
Ela nao substitui a previsao diaria de curto prazo e deve ser interpretada em
conjunto com os dados de risco de incendio, focos de calor e observacao local.
"""
        )

    st.markdown("#### Tendencia climatica - Proximos 3 meses")
    _render_trend_block(
        title="Proximos 3 meses",
        text=_trend_text_3_months(local_name, region),
        reference=_month_window(reference_day, 3),
        generated_at=generated_at,
    )

    st.markdown("#### Tendencia climatica - Proximos 6 meses")
    _render_trend_block(
        title="Proximos 6 meses",
        text=_trend_text_6_months(local_name, region),
        reference=_month_window(reference_day, 6),
        generated_at=generated_at,
    )

    st.caption(
        "Use esta tendencia como suporte ao planejamento e refine a decisao com a previsao de 16 dias "
        "e as camadas de deteccao ativa do mapa operacional."
    )
