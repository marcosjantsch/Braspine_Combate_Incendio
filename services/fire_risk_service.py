# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Dict, Optional

from shapely.geometry import shape

from services.gee_service import ee, initialize_earth_engine
from services.weather_service import fetch_weather_window

VIS_FIRE_RISK = {
    "min": 0,
    "max": 100,
    "palette": ["#1a9850", "#ffff00", "#fdae61", "#d7191c"],
    "opacity": 0.45,
}


def _roi_geometry(roi_geojson: Optional[Dict]):
    if not roi_geojson:
        raise ValueError("ROI não informada.")
    return ee.Geometry(roi_geojson)


def _score(image, low: float, high: float):
    return image.subtract(low).divide(high - low).clamp(0, 1)


def _safe_image(value: float):
    return ee.Image.constant(value)


def classify_risk(value: float | None) -> str:
    if value is None:
        return "Sem dados"
    if value < 25:
        return "Baixo"
    if value < 50:
        return "Moderado"
    if value < 75:
        return "Alto"
    return "Muito alto"


def _reference_date(reference_datetime: str | None = None) -> date:
    if not reference_datetime:
        return date.today()
    try:
        parsed = datetime.fromisoformat(reference_datetime)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.date()
    except Exception:
        return date.today()


def _roi_center(roi_geojson: Dict) -> tuple[float, float]:
    geom = shape(roi_geojson)
    if geom.is_empty:
        raise ValueError("ROI vazia.")
    center = geom.centroid
    return float(center.y), float(center.x)


def _valid_numbers(values) -> list[float]:
    numbers: list[float] = []
    for value in values or []:
        try:
            if value is None:
                continue
            numbers.append(float(value))
        except (TypeError, ValueError):
            continue
    return numbers


def _mean(values, default: float | None = None) -> float | None:
    numbers = _valid_numbers(values)
    if not numbers:
        return default
    return sum(numbers) / len(numbers)


def _sum(values, default: float = 0.0) -> float:
    numbers = _valid_numbers(values)
    if not numbers:
        return default
    return sum(numbers)


def _max(values, default: float | None = None) -> float | None:
    numbers = _valid_numbers(values)
    if not numbers:
        return default
    return max(numbers)


def _min(values, default: float | None = None) -> float | None:
    numbers = _valid_numbers(values)
    if not numbers:
        return default
    return min(numbers)


def _score_value(value: float | None, low: float, high: float) -> float:
    if value is None:
        return 0.0
    if high == low:
        return 0.0
    return max(0.0, min(1.0, (float(value) - low) / (high - low)))


def _inverse_score_value(value: float | None, low: float, high: float) -> float:
    return 1.0 - _score_value(value, low, high)


def _dry_day_ratio(precipitation_values) -> float:
    values = _valid_numbers(precipitation_values)
    if not values:
        return 0.0
    dry_days = sum(1 for value in values if value < 1.0)
    return dry_days / len(values)


def _weather_fallback_fire_risk(roi_geojson: Dict, days: int, reference_datetime: str | None, ee_message: str) -> Dict:
    try:
        lat, lon = _roi_center(roi_geojson)
        end = _reference_date(reference_datetime)
        period_days = max(1, min(int(days), 30))
        start = end - timedelta(days=period_days - 1)
        payload = fetch_weather_window(lat, lon, start, days=period_days)
        daily = payload.get("daily") or {}
        hourly = payload.get("hourly") or {}

        max_temperature = _mean(daily.get("temperature_2m_max"))
        if max_temperature is None:
            max_temperature = _max(hourly.get("temperature_2m"))
        min_humidity = _min(hourly.get("relative_humidity_2m"))
        precipitation_sum = _sum(daily.get("precipitation_sum") or hourly.get("precipitation"))
        max_wind = _max(daily.get("wind_speed_10m_max") or hourly.get("wind_speed_10m"))
        max_gust = _max(daily.get("wind_gusts_10m_max") or hourly.get("wind_gusts_10m"))
        wind_reference = max(value for value in [max_wind or 0.0, max_gust or 0.0])

        temperature_score = _score_value(max_temperature, 24.0, 40.0)
        humidity_score = _inverse_score_value(min_humidity, 18.0, 55.0)
        rain_score = _inverse_score_value(precipitation_sum, 5.0, 80.0)
        wind_score = _score_value(wind_reference, 8.0, 38.0)
        dry_days_score = _dry_day_ratio(daily.get("precipitation_sum"))

        risk_value = round(
            (
                temperature_score * 28.0
                + humidity_score * 24.0
                + rain_score * 24.0
                + wind_score * 14.0
                + dry_days_score * 10.0
            ),
            1,
        )
        status_parts = [
            "Indice de risco calculado por fallback meteorologico Open-Meteo.",
            f"Periodo: {start.isoformat()} a {end.isoformat()}.",
            f"Temperatura media maxima: {max_temperature:.1f} C." if max_temperature is not None else "",
            f"Umidade minima: {min_humidity:.0f}%." if min_humidity is not None else "",
            f"Chuva acumulada: {precipitation_sum:.1f} mm.",
            f"Vento maximo: {wind_reference:.1f} km/h." if wind_reference else "",
            f"Earth Engine indisponivel: {ee_message}",
        ]
        return {
            "fire_risk_image": None,
            "risk_value": risk_value,
            "risk_class": classify_risk(risk_value),
            "goes_image": None,
            "goes_datetime": "",
            "goes_hotspot_image": None,
            "viirs_points": None,
            "risk_period": f"{start.isoformat()} a {end.isoformat()}",
            "status": " ".join(part for part in status_parts if part),
        }
    except Exception as exc:
        return {
            "fire_risk_image": None,
            "risk_value": None,
            "risk_class": "Sem dados",
            "goes_image": None,
            "goes_datetime": "",
            "goes_hotspot_image": None,
            "viirs_points": None,
            "status": f"{ee_message} Fallback meteorologico indisponivel: {exc}",
        }


def build_fire_risk_index(roi_geojson: Dict, days: int = 30, reference_datetime: str | None = None) -> Dict:
    ok, message = initialize_earth_engine()
    if not ok or ee is None:
        return _weather_fallback_fire_risk(roi_geojson, days, reference_datetime, message)

    roi = _roi_geometry(roi_geojson)
    end = _reference_date(reference_datetime)
    start = end - timedelta(days=days)
    start_recent = end - timedelta(days=7)

    try:
        era5 = ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR").filterDate(start.isoformat(), end.isoformat()).filterBounds(roi)
        temperature_c = era5.select("temperature_2m").mean().subtract(273.15).clip(roi)
        dewpoint_c = era5.select("dewpoint_temperature_2m").mean().subtract(273.15).clip(roi)
        rain_mm = era5.select("total_precipitation_sum").sum().multiply(1000).clip(roi)
        soil_water = era5.select("volumetric_soil_water_layer_1").mean().clip(roi)

        modis_lst = (
            ee.ImageCollection("MODIS/061/MOD11A1")
            .filterDate(start_recent.isoformat(), end.isoformat())
            .filterBounds(roi)
            .select("LST_Day_1km")
            .mean()
            .multiply(0.02)
            .subtract(273.15)
            .clip(roi)
        )

        s2 = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterDate(start.isoformat(), end.isoformat())
            .filterBounds(roi)
            .filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", 50))
            .median()
            .clip(roi)
        )
        ndvi = s2.normalizedDifference(["B8", "B4"]).rename("NDVI")
        ndwi = s2.normalizedDifference(["B3", "B8"]).rename("NDWI")

        humidity_proxy = dewpoint_c.subtract(temperature_c).multiply(-1)
        temp_score = _score(temperature_c.max(modis_lst), 24, 42)
        low_humidity_score = _score(humidity_proxy, 4, 18)
        no_rain_score = ee.Image.constant(1).subtract(_score(rain_mm, 5, 80))
        water_deficit_score = ee.Image.constant(1).subtract(_score(soil_water, 0.12, 0.36))
        dry_vegetation_score = ee.Image.constant(1).subtract(_score(ndvi, 0.25, 0.75))
        low_ndwi_score = ee.Image.constant(1).subtract(_score(ndwi, -0.1, 0.35))

        viirs_collection = (
            ee.ImageCollection("NASA/LANCE/NOAA20_VIIRS/C2")
            .merge(ee.ImageCollection("NASA/LANCE/SNPP_VIIRS/C2"))
            .filterDate(start_recent.isoformat(), end.isoformat())
            .filterBounds(roi)
        )
        viirs_count = viirs_collection.size()
        viirs_score = ee.Image.constant(viirs_count.min(20).divide(20)).clip(roi)
        viirs_points = viirs_collection.select("frp").max().selfMask().sample(
            region=roi,
            scale=375,
            numPixels=500,
            geometries=True,
        )

        fire_risk_index = (
            temp_score.multiply(18)
            .add(low_humidity_score.multiply(16))
            .add(no_rain_score.multiply(18))
            .add(water_deficit_score.multiply(16))
            .add(dry_vegetation_score.multiply(14))
            .add(low_ndwi_score.multiply(10))
            .add(viirs_score.multiply(8))
            .rename("fire_risk_index")
            .clamp(0, 100)
            .clip(roi)
        )
        risk_info = fire_risk_index.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=roi,
            scale=1000,
            maxPixels=1_000_000_000,
            bestEffort=True,
        ).getInfo()
        risk_value = risk_info.get("fire_risk_index")
        risk_value = round(float(risk_value), 1) if risk_value is not None else None

        return {
            "fire_risk_image": fire_risk_index,
            "risk_value": risk_value,
            "risk_class": classify_risk(risk_value),
            "goes_image": None,
            "goes_datetime": "",
            "goes_hotspot_image": None,
            "viirs_points": viirs_points,
            "risk_period": f"{start.isoformat()} a {end.isoformat()}",
            "status": f"Indice de risco de incendio gerado para o periodo de {days} dias ate {end.isoformat()}.",
        }
    except Exception as exc:
        return {
            "fire_risk_image": _safe_image(0).rename("fire_risk_index").clip(roi),
            "risk_value": None,
            "risk_class": "Sem dados",
            "goes_image": None,
            "goes_datetime": "",
            "goes_hotspot_image": None,
            "viirs_points": ee.FeatureCollection([]),
            "status": f"Falha ao gerar índice de risco: {exc}",
        }
