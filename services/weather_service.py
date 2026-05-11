# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, Iterable
from urllib.parse import urlencode

import requests

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
TIMEZONE = "America/Sao_Paulo"

HOURLY_FORECAST_FIELDS = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "precipitation_probability",
    "precipitation",
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
    "cloud_cover",
    "shortwave_radiation",
]

DAILY_FORECAST_FIELDS = [
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "precipitation_probability_max",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "uv_index_max",
]

HOURLY_ARCHIVE_FIELDS = [
    field
    for field in HOURLY_FORECAST_FIELDS
    if field != "precipitation_probability"
]

DAILY_ARCHIVE_FIELDS = [
    field
    for field in DAILY_FORECAST_FIELDS
    if field != "precipitation_probability_max"
]

DAILY_TREND_FIELDS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "wind_speed_10m_max",
]

WEATHER_CODE_LABELS = {
    0: "Ceu limpo",
    1: "Poucas nuvens",
    2: "Parcialmente nublado",
    3: "Nublado",
    45: "Nevoeiro",
    48: "Nevoeiro com deposicao",
    51: "Garoa fraca",
    53: "Garoa moderada",
    55: "Garoa intensa",
    61: "Chuva fraca",
    63: "Chuva moderada",
    65: "Chuva forte",
    80: "Pancadas fracas",
    81: "Pancadas moderadas",
    82: "Pancadas fortes",
    95: "Trovoadas",
    96: "Trovoadas com granizo",
    99: "Trovoadas severas",
}


class WeatherServiceError(RuntimeError):
    pass


def _request_json(url: str, params: Dict) -> Dict:
    response = requests.get(f"{url}?{urlencode(params, doseq=True)}", timeout=25)
    if response.status_code >= 400:
        raise WeatherServiceError(f"Servico meteorologico retornou HTTP {response.status_code}.")
    data = response.json()
    if "error" in data:
        raise WeatherServiceError(str(data.get("reason") or data["error"]))
    return data


def _join_fields(fields: Iterable[str]) -> str:
    return ",".join(fields)


def fetch_weather_forecast(lat: float, lon: float, forecast_days: int = 16) -> Dict:
    params = {
        "latitude": round(float(lat), 6),
        "longitude": round(float(lon), 6),
        "hourly": _join_fields(HOURLY_FORECAST_FIELDS),
        "daily": _join_fields(DAILY_FORECAST_FIELDS),
        "current": _join_fields(
            [
                "temperature_2m",
                "relative_humidity_2m",
                "precipitation",
                "weather_code",
                "wind_speed_10m",
                "wind_direction_10m",
                "wind_gusts_10m",
            ]
        ),
        "forecast_days": min(max(int(forecast_days), 1), 16),
        "timezone": TIMEZONE,
    }
    data = _request_json(FORECAST_URL, params)
    data["status"] = "Previsao meteorologica carregada."
    return data


def _fetch_weather_range(lat: float, lon: float, start_day: date, end_day: date, forecast: bool) -> Dict:
    params = {
        "latitude": round(float(lat), 6),
        "longitude": round(float(lon), 6),
        "hourly": _join_fields(HOURLY_FORECAST_FIELDS if forecast else HOURLY_ARCHIVE_FIELDS),
        "daily": _join_fields(DAILY_FORECAST_FIELDS if forecast else DAILY_ARCHIVE_FIELDS),
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "timezone": TIMEZONE,
    }
    return _request_json(FORECAST_URL if forecast else ARCHIVE_URL, params)


def _merge_weather_payloads(payloads: list[Dict], status: str) -> Dict:
    merged: Dict = {"hourly": {}, "daily": {}, "status": status}
    for group_name in ("hourly", "daily"):
        group_keys = set()
        for payload in payloads:
            group_keys.update((payload.get(group_name) or {}).keys())
        for payload in payloads:
            group = payload.get(group_name) or {}
            group_len = len(group.get("time") or next(iter(group.values()), []))
            for key in group_keys:
                values = group.get(key)
                if values is None:
                    values = [None] * group_len
                merged[group_name].setdefault(key, [])
                merged[group_name][key].extend(values if isinstance(values, list) else [values])
    return merged


def fetch_weather_window(lat: float, lon: float, reference_day: date, days: int = 16) -> Dict:
    start_day = reference_day
    end_day = reference_day + timedelta(days=max(int(days) - 1, 0))
    today = date.today()
    payloads: list[Dict] = []

    if start_day < today:
        archive_end = min(end_day, today - timedelta(days=1))
        if archive_end >= start_day:
            payloads.append(_fetch_weather_range(lat, lon, start_day, archive_end, forecast=False))

    if end_day >= today:
        forecast_start = max(start_day, today)
        if forecast_start <= end_day:
            payloads.append(_fetch_weather_range(lat, lon, forecast_start, end_day, forecast=True))

    if not payloads:
        raise WeatherServiceError("Nao foi possivel montar a janela meteorologica.")

    data = _merge_weather_payloads(
        payloads,
        f"Previsao meteorologica carregada para {start_day.isoformat()} a {end_day.isoformat()}.",
    )
    data["forecast_start_date"] = start_day.isoformat()
    data["forecast_end_date"] = end_day.isoformat()
    return data


def fetch_climate_trend(lat: float, lon: float, reference_day: date, days: int = 30) -> Dict:
    end_day = min(reference_day, date.today() - timedelta(days=1))
    start_day = end_day - timedelta(days=max(int(days) - 1, 1))
    params = {
        "latitude": round(float(lat), 6),
        "longitude": round(float(lon), 6),
        "daily": _join_fields(DAILY_TREND_FIELDS),
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "timezone": TIMEZONE,
    }
    data = _request_json(ARCHIVE_URL, params)
    data["trend_start_date"] = start_day.isoformat()
    data["trend_end_date"] = end_day.isoformat()
    data["status"] = "Tendencia climatica carregada."
    return data


def weather_code_label(code) -> str:
    try:
        return WEATHER_CODE_LABELS.get(int(code), f"Codigo {int(code)}")
    except Exception:
        return "-"
