# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Dict, Optional

from services.gee_service import ee, initialize_earth_engine

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


def build_fire_risk_index(roi_geojson: Dict, days: int = 30, reference_datetime: str | None = None) -> Dict:
    ok, message = initialize_earth_engine()
    if not ok or ee is None:
        return {
            "fire_risk_image": None,
            "goes_image": None,
            "goes_datetime": "",
            "viirs_points": None,
            "status": message,
        }

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
