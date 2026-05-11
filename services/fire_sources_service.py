# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import geopandas as gpd
import pandas as pd
import requests
from pyproj import Geod, Transformer
from shapely.geometry import Point, shape
from shapely.ops import nearest_points

from core.alert_rules import alert_distance_for_uf, table_distance_for_uf
from core.config import BASE_DIR
from core.time_context import format_datetime_brasilia, format_datetime_zulu, format_period_brasilia, format_period_zulu
from services.gee_service import build_tile_url, ee, initialize_earth_engine

TEMPORAL_WINDOW_HOURS = 24
ACTIVE_FIRE_WINDOW_HOURS = 48
CURRENT_ACTIVE_FIRE_WINDOW_HOURS = 1.5
NOAA_HMS_SMOKE_BASE_URL = "https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Smoke_Polygons/Shapefile"
NOAA_HMS_CACHE_DIR = BASE_DIR / "data" / "cache" / "noaa_hms_smoke"
NASA_GIBS_WMS_URL = "https://gibs.earthdata.nasa.gov/wms/epsg3857/best/wms.cgi"
INPE_QUEIMADAS_DAILY_URL = "https://dataserver-coids.inpe.br/queimadas/queimadas/focos/csv/diario/Brasil/focos_diario_br_{day:%Y%m%d}.csv"
INPE_QUEIMADAS_CACHE_DIR = BASE_DIR / "data" / "cache" / "inpe_queimadas"
GEOD = Geod(ellps="WGS84")
WEB_TO_WGS84 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
WIND_TOWARDS_FARM_TOLERANCE_DEG = 45.0

FIRE_DATA_SOURCES: Dict[str, Dict[str, object]] = {
    "goes_visual": {
        "label": "GOES visual meteorologico",
        "name": "GOES visual/termal",
        "type": "termal",
        "priority": 1,
        "distance": False,
        "alert": False,
        "window_hours": 24,
        "query": "goes_visual",
        "collections": ["NOAA/GOES/19/MCMIPF", "NOAA/GOES/16/MCMIPF"],
        "vis": {"bands": ["CMI_C02", "CMI_C03", "CMI_C01"], "min": 0.0, "max": 0.8, "gamma": 1.25},
        "opacity": 0.72,
    },
    "goes_thermal": {
        "label": "GOES temperatura de brilho",
        "name": "GOES temperatura de brilho",
        "type": "termal",
        "priority": 1,
        "distance": False,
        "alert": False,
        "window_hours": 24,
        "query": "goes_thermal",
        "collections": ["NOAA/GOES/19/MCMIPF", "NOAA/GOES/16/MCMIPF"],
        "vis": {"bands": ["CMI_C13"], "min": 190, "max": 330, "palette": ["#7f00ff", "#004cff", "#00ffff", "#ffff00", "#ff0000"]},
        "opacity": 0.55,
    },
    "goes_fdcf": {
        "label": "GOES hotspots recentes",
        "aliases": ["GOES-16 Hot Spot"],
        "name": "GOES Hotspot/FDCF",
        "type": "hotspot",
        "priority": 1,
        "distance": True,
        "alert": True,
        "window_hours": ACTIVE_FIRE_WINDOW_HOURS,
        "query": "goes_fdcf",
        "collections": ["NOAA/GOES/19/FDCF", "NOAA/GOES/16/FDCF"],
        "band": "Power",
        "vis": {"min": 0, "max": 400, "palette": ["#ffff00", "#ff9900", "#ff0000", "#ff00ff"]},
        "opacity": 0.88,
        "sample_scale": 2000,
        "sample_limit": 5000,
        "event_type": "Hotspot GOES",
        "satellite": "GOES",
    },
    "firms_modis": {
        "label": "FIRMS MODIS",
        "name": "FIRMS MODIS",
        "type": "hotspot",
        "priority": 3,
        "distance": True,
        "alert": True,
        "window_hours": ACTIVE_FIRE_WINDOW_HOURS,
        "query": "image_collection_max",
        "collection": "FIRMS",
        "band": "confidence",
        "vis": {"min": 30, "max": 100, "palette": ["#ffd400", "#ff6600", "#d40000"]},
        "opacity": 0.95,
        "sample_scale": 1000,
        "sample_limit": 5000,
        "event_type": "Foco de calor",
        "satellite": "MODIS",
    },
    "viirs_375": {
        "label": "VIIRS 375 m",
        "name": "VIIRS 375 m",
        "type": "hotspot",
        "priority": 2,
        "distance": True,
        "alert": True,
        "window_hours": ACTIVE_FIRE_WINDOW_HOURS,
        "query": "viirs_375",
        "collections": ["NASA/LANCE/NOAA20_VIIRS/C2", "NASA/LANCE/SNPP_VIIRS/C2"],
        "fallback_collection": "NASA/VIIRS/002/VNP14A1",
        "band": "frp",
        "vis": {"min": 0, "max": 80, "palette": ["#ffff00", "#ff7a00", "#ff0000", "#ffffff"]},
        "opacity": 0.92,
        "sample_scale": 375,
        "sample_limit": 5000,
        "event_type": "Hotspot",
        "satellite": "VIIRS",
    },
    "nasa_gibs_hotspots": {
        "label": "NASA GIBS Hotspots",
        "name": "NASA GIBS Hotspots",
        "type": "hotspot",
        "priority": 2,
        "distance": True,
        "alert": True,
        "requires_ee": False,
        "window_hours": ACTIVE_FIRE_WINDOW_HOURS,
        "query": "nasa_gibs_hotspots",
        "gibs_layers": [
            {
                "name": "NASA GIBS | VIIRS SNPP hotspots",
                "layer": "VIIRS_SNPP_Thermal_Anomalies_375m_All",
                "source": "NASA GIBS VIIRS S-NPP Thermal Anomalies 375m",
            },
            {
                "name": "NASA GIBS | VIIRS NOAA-20 hotspots",
                "layer": "VIIRS_NOAA20_Thermal_Anomalies_375m_All",
                "source": "NASA GIBS VIIRS NOAA-20 Thermal Anomalies 375m",
            },
            {
                "name": "NASA GIBS | MODIS hotspots",
                "layer": "MODIS_Combined_Thermal_Anomalies_All",
                "source": "NASA GIBS MODIS Combined Thermal Anomalies",
            },
        ],
        "point_sources": [
            {
                "collections": ["NASA/LANCE/NOAA20_VIIRS/C2", "NASA/LANCE/SNPP_VIIRS/C2"],
                "band": "frp",
                "rename": "NASA_GIBS_VIIRS_FRP",
                "satellite": "NASA GIBS VIIRS",
                "event_type": "Hotspot NASA GIBS VIIRS",
                "sample_scale": 375,
                "sample_limit": 5000,
                "vis": {"min": 0, "max": 80, "palette": ["#ffff00", "#ff7a00", "#ff0000", "#ffffff"]},
            },
            {
                "collections": ["FIRMS"],
                "band": "confidence",
                "rename": "NASA_GIBS_MODIS_CONFIDENCE",
                "satellite": "NASA GIBS MODIS",
                "event_type": "Foco NASA GIBS MODIS",
                "sample_scale": 1000,
                "sample_limit": 5000,
                "vis": {"min": 30, "max": 100, "palette": ["#ffd400", "#ff6600", "#d40000"]},
            },
        ],
        "opacity": 0.95,
        "event_type": "Hotspot NASA GIBS",
        "satellite": "NASA GIBS",
    },
    "modis_firemask": {
        "label": "MODIS Terra FireMask",
        "name": "MODIS Terra FireMask",
        "type": "termal",
        "priority": 5,
        "distance": True,
        "alert": False,
        "window_hours": ACTIVE_FIRE_WINDOW_HOURS,
        "query": "image_collection_max",
        "collection": "MODIS/061/MOD14A1",
        "band": "MaxFRP",
        "vis": {"min": 0, "max": 80, "palette": ["#fff200", "#ff8c00", "#ff0000", "#ffffff"]},
        "opacity": 0.90,
        "sample_scale": 1000,
        "sample_limit": 5000,
        "event_type": "Anomalia termica",
        "satellite": "MODIS Terra",
    },
    "modis_burned_area": {
        "label": "MODIS Burned Area",
        "name": "MODIS Burned Area",
        "type": "historico",
        "priority": 20,
        "distance": False,
        "alert": False,
        "window_days": 1,
        "query": "image_collection_max",
        "collection": "MODIS/061/MCD64A1",
        "band": "BurnDate",
        "vis": {"min": 1, "max": 366, "palette": ["#5b1300", "#d9480f", "#ffd166"]},
        "opacity": 0.55,
        "sample_scale": None,
        "event_type": "Area queimada",
        "satellite": "MODIS",
    },
    "sentinel2": {
        "label": "Sentinel-2 NDVI/NBR",
        "name": "Sentinel-2 NDVI",
        "type": "vegetacao",
        "priority": 30,
        "distance": False,
        "alert": False,
        "window_days": 1,
        "query": "sentinel2_ndvi",
        "collection": "COPERNICUS/S2_SR_HARMONIZED",
        "opacity": 0.55,
    },
    "landsat_thermal": {
        "label": "Landsat Collection 2 Thermal",
        "name": "Landsat termal",
        "type": "termal_contexto",
        "priority": 35,
        "distance": False,
        "alert": False,
        "window_days": 1,
        "query": "landsat_thermal",
        "collection": "LANDSAT/LC08/C02/T1_L2",
        "opacity": 0.50,
    },
    "era5_land": {
        "label": "ERA5 Land",
        "name": "ERA5 Land temperatura",
        "type": "risco",
        "priority": 40,
        "distance": False,
        "alert": False,
        "window_days": 1,
        "query": "era5_temperature",
        "collection": "ECMWF/ERA5_LAND/DAILY_AGGR",
        "opacity": 0.45,
    },
    "inpe_queimadas": {
        "label": "INPE Queimadas",
        "name": "INPE Queimadas",
        "type": "hotspot",
        "priority": 4,
        "distance": True,
        "alert": False,
        "requires_ee": False,
        "window_hours": ACTIVE_FIRE_WINDOW_HOURS,
        "query": "inpe_queimadas",
        "event_type": "Foco INPE Queimadas",
        "satellite": "BDQueimadas",
        "sample_limit": 10000,
    },
    "noaa_hms_smoke": {"label": "NOAA HMS Smoke", "name": "NOAA HMS Smoke", "type": "fumaca", "priority": 50, "distance": True, "alert": False, "window_hours": ACTIVE_FIRE_WINDOW_HOURS, "query": "noaa_hms_smoke", "requires_ee": False, "event_type": "Fumaca detectada", "satellite": "NOAA HMS"},
    "cams": {"label": "CAMS aerossois/fumaca", "name": "CAMS/Sentinel-5P fumaca e aerossois", "type": "fumaca", "priority": 55, "distance": False, "alert": False, "window_hours": ACTIVE_FIRE_WINDOW_HOURS, "query": "smoke_aerosol_context"},
    "sentinel3_slstr": {"label": "Sentinel-3 SLSTR", "name": "Sentinel-3 SLSTR", "type": "termal", "priority": 36, "distance": False, "alert": False, "window_days": 1, "query": "unconfigured"},
    "ecmwf_fwi": {"label": "ECMWF Fire Weather Index", "name": "ECMWF FWI", "type": "risco", "priority": 41, "distance": False, "alert": False, "window_days": 1, "query": "unconfigured"},
    "goes_glm": {"label": "GOES GLM Lightning", "name": "GOES GLM Lightning", "type": "raio", "priority": 60, "distance": False, "alert": False, "window_days": 1, "query": "unconfigured"},
    "smap": {"label": "SMAP umidade do solo", "name": "SMAP umidade do solo", "type": "risco", "priority": 42, "distance": False, "alert": False, "window_days": 1, "query": "unconfigured"},
}


def source_labels() -> Dict[str, str]:
    labels = {}
    for key, config in FIRE_DATA_SOURCES.items():
        labels[str(config["label"])] = key
        for alias in config.get("aliases", []):
            labels[str(alias)] = key
    return labels


def get_temporal_window(reference_date: str | datetime | Dict, hours: float = TEMPORAL_WINDOW_HOURS) -> Tuple[datetime, datetime, datetime]:
    if isinstance(reference_date, dict) and reference_date.get("start") and reference_date.get("end"):
        start = datetime.fromisoformat(str(reference_date["start"]))
        end = datetime.fromisoformat(str(reference_date["end"]))
        reference = datetime.fromisoformat(str(reference_date.get("reference") or reference_date["end"]))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc), reference.astimezone(timezone.utc)
    if isinstance(reference_date, datetime):
        reference = reference_date
    else:
        reference = datetime.fromisoformat(reference_date)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    reference = reference.astimezone(timezone.utc)
    return reference - timedelta(hours=float(hours)), reference, reference


def _window_label(start: datetime, end: datetime) -> str:
    hours = max(1, round((end - start).total_seconds() / 3600))
    return f"ultimas {hours} horas da referencia"


def _log(source_key: str, source: Dict, reference: datetime, start: datetime, end: datetime, count: int, status: str, message: str) -> Dict:
    return {
        "consulta": format_datetime_brasilia(datetime.now(timezone.utc)),
        "referencia": format_datetime_brasilia(reference),
        "referencia_zulu": format_datetime_zulu(reference),
        "janela": format_period_brasilia(start, end),
        "janela_zulu": format_period_zulu(start, end),
        "fonte": source["name"],
        "source_key": source_key,
        "quantidade": int(count),
        "status": status,
        "mensagem": message,
    }


def _roi_geometry(roi_geojson: Optional[Dict]):
    return ee.Geometry(roi_geojson) if roi_geojson else None


def _image_time(image) -> str:
    try:
        millis = image.get("system:time_start").getInfo()
        return format_datetime_brasilia(datetime.fromtimestamp(millis / 1000, tz=timezone.utc)) if millis else ""
    except Exception:
        return ""


def _image_time_zulu(image) -> str:
    try:
        millis = image.get("system:time_start").getInfo()
        return format_datetime_zulu(datetime.fromtimestamp(millis / 1000, tz=timezone.utc)) if millis else ""
    except Exception:
        return ""


def _nearest_image(collection, reference: datetime):
    reference_ms = int(reference.timestamp() * 1000)

    def set_delta(image):
        return image.set(
            "time_delta_ms",
            ee.Number(image.get("system:time_start")).subtract(reference_ms).abs(),
        )

    return collection.map(set_delta).sort("time_delta_ms").first()


def _sample_hotspot_points(image, roi, source_key: str, source: Dict) -> List[Dict]:
    scale = source.get("sample_scale")
    if not scale:
        return []
    band = str(source.get("band"))
    sample_limit = int(source.get("sample_limit", 2000))
    try:
        masked_image = image.rename(band).updateMask(image.rename(band).gt(float(source.get("min_detection_value", 0)))).selfMask()
        features = (
            masked_image.sample(region=roi, scale=int(scale), geometries=True, dropNulls=True, tileScale=4)
            .limit(sample_limit)
            .getInfo()
            .get("features", [])
        )
    except Exception:
        return []
    points = []
    for feature in features:
        coords = feature.get("geometry", {}).get("coordinates")
        if not coords or len(coords) < 2:
            continue
        points.append(
            {
                "lon": float(coords[0]),
                "lat": float(coords[1]),
                "source": source["name"],
                "source_key": source_key,
                "satellite": source.get("satellite", source["name"]),
                "event_type": source.get("event_type", source["type"]),
                "alert_capable": bool(source.get("alert")),
                "distance_capable": bool(source.get("distance")),
                "priority": int(source.get("priority", 99)),
                "geometry_type": "point",
                "value": feature.get("properties", {}).get(band),
                "detection_datetime": source.get("detection_datetime", ""),
                "detection_datetime_zulu": source.get("detection_datetime_zulu", ""),
                "detection_period": source.get("detection_period", ""),
            }
        )
    return points


def _geometry_detections(gdf, source_key: str, source: Dict, satellite: str, event_type: str) -> List[Dict]:
    detections: List[Dict] = []
    if gdf is None or gdf.empty:
        return detections
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        representative = geom.representative_point()
        props = {key: str(row.get(key, "")) for key in row.index if key != "geometry"}
        detection_datetime = props.get("Start") or props.get("START") or props.get("start") or source.get("detection_datetime", "")
        detections.append(
            {
                "lon": float(representative.x),
                "lat": float(representative.y),
                "geometry_geojson": geom.__geo_interface__,
                "geometry_type": geom.geom_type,
                "source": source["name"],
                "source_key": source_key,
                "satellite": satellite,
                "event_type": event_type,
                "alert_capable": bool(source.get("alert")),
                "distance_capable": bool(source.get("distance")),
                "priority": int(source.get("priority", 99)),
                "properties": props,
                "detection_datetime": detection_datetime,
                "detection_datetime_zulu": source.get("detection_datetime_zulu", detection_datetime),
                "detection_period": source.get("detection_period", ""),
            }
        )
    return detections


def _empty_result(source_key: str, source: Dict, reference: datetime, start: datetime, end: datetime, status: str, message: str) -> Dict:
    return {
        "source_key": source_key,
        "source": source,
        "layers": [],
        "points": [],
        "count": 0,
        "image_datetime": "",
        "image_datetime_zulu": "",
        "status": status,
        "message": message,
        "log": _log(source_key, source, reference, start, end, 0, status, message),
    }


def _image_collection_max(source_key: str, source: Dict, roi, start: datetime, end: datetime, reference: datetime) -> Dict:
    collection = ee.ImageCollection(str(source["collection"])).filterBounds(roi).filterDate(start.isoformat(), end.isoformat())
    count = int(collection.size().getInfo())
    if count == 0:
        return _empty_result(source_key, source, reference, start, end, "ignorado", f"Sem imagens nas {_window_label(start, end)}.")

    band = str(source["band"])
    image = collection.select(band).max().selfMask().rename(band)
    layer = {
        "name": f"GE | {source['name']}",
        "url": build_tile_url(image, source["vis"]),
        "opacity": float(source.get("opacity", 0.75)),
        "source": source["name"],
        "indicator": source["label"],
        "image_datetime": _image_time(collection.sort("system:time_start", False).first()),
        "image_datetime_zulu": _image_time_zulu(collection.sort("system:time_start", False).first()),
        "period": format_period_brasilia(start, end),
        "period_zulu": format_period_zulu(start, end),
        "composition": f"{band} maximo nas {_window_label(start, end)}",
        "source_key": source_key,
    }
    sample_source = dict(source)
    sample_source.update(
        {
            "detection_datetime": layer["image_datetime"],
            "detection_datetime_zulu": layer["image_datetime_zulu"],
            "detection_period": layer["period"],
        }
    )
    points = _sample_hotspot_points(image, roi, source_key, sample_source) if source.get("distance") else []
    return {
        "source_key": source_key,
        "source": source,
        "layers": [layer],
        "points": points,
        "count": count,
        "image_datetime": layer["image_datetime"],
        "status": "plotado",
        "message": f"{count} imagem(ns) encontrada(s); {len(points)} ponto(s) amostrado(s).",
        "log": _log(source_key, source, reference, start, end, count, "plotado", f"Camada plotada; {len(points)} ponto(s) amostrado(s)."),
    }


def _hms_zip_url(day: datetime.date) -> str:
    return f"{NOAA_HMS_SMOKE_BASE_URL}/{day:%Y}/{day:%m}/hms_smoke{day:%Y%m%d}.zip"


def _hms_cache_path(day: datetime.date) -> Path:
    return NOAA_HMS_CACHE_DIR / f"hms_smoke{day:%Y%m%d}.zip"


def _download_hms_zip(day: datetime.date) -> Optional[Path]:
    NOAA_HMS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _hms_cache_path(day)
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return cache_path
    response = requests.get(_hms_zip_url(day), timeout=30)
    if response.status_code != 200 or not response.content:
        return None
    cache_path.write_bytes(response.content)
    return cache_path


def _hms_candidate_days(start: datetime, end: datetime) -> List[datetime.date]:
    days: List[datetime.date] = []
    current = end.date()
    while current >= start.date():
        days.append(current)
        current = current - timedelta(days=1)
    return days


def _inpe_daily_url(day) -> str:
    return INPE_QUEIMADAS_DAILY_URL.format(day=day)


def _inpe_cache_path(day) -> Path:
    return INPE_QUEIMADAS_CACHE_DIR / f"focos_diario_br_{day:%Y%m%d}.csv"


def _download_inpe_daily_csv(day) -> Optional[Path]:
    INPE_QUEIMADAS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _inpe_cache_path(day)
    current_utc_day = datetime.now(timezone.utc).date()
    if cache_path.exists() and cache_path.stat().st_size > 0:
        if day != current_utc_day:
            return cache_path
        cache_age_seconds = datetime.now(timezone.utc).timestamp() - cache_path.stat().st_mtime
        if cache_age_seconds < 600:
            return cache_path

    response = requests.get(
        _inpe_daily_url(day),
        timeout=45,
        headers={"User-Agent": "fire-monitoring-platform/1.0"},
    )
    if response.status_code != 200 or not response.content:
        return None
    first_line = response.content[:250].decode("utf-8", errors="ignore").lower()
    if "lat" not in first_line or "lon" not in first_line:
        return None
    cache_path.write_bytes(response.content)
    return cache_path


def _safe_float(value) -> Optional[float]:
    try:
        if pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _angle_difference(a: float, b: float) -> float:
    return abs((float(a) - float(b) + 180.0) % 360.0 - 180.0)


def _compass_label(degrees: Optional[float]) -> str:
    if degrees is None:
        return ""
    labels = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    index = int((float(degrees) + 22.5) // 45) % 8
    return labels[index]


def _format_wind_direction(degrees: Optional[float]) -> str:
    if degrees is None:
        return ""
    return f"{float(degrees) % 360:.0f} graus {_compass_label(degrees)}"


def _wind_to_farm_analysis(
    focus_lon: float,
    focus_lat: float,
    farm_lon: float,
    farm_lat: float,
    wind_context: Optional[Dict],
) -> Dict:
    wind_context = wind_context or {}
    wind_from = _safe_float(wind_context.get("direction_deg"))
    wind_speed = _safe_float(wind_context.get("speed_kmh"))
    if wind_from is None:
        return {
            "wind_direction": "",
            "wind_speed_kmh": "",
            "wind_to_farm": None,
            "wind_to_farm_label": "Sem dados",
            "wind_alignment_deg": "",
            "wind_bearing_to_farm_deg": "",
            "wind_source": wind_context.get("source", ""),
        }

    bearing, _, _ = GEOD.inv(float(focus_lon), float(focus_lat), float(farm_lon), float(farm_lat))
    bearing_to_farm = bearing % 360.0
    wind_towards_direction = (wind_from + 180.0) % 360.0
    alignment = _angle_difference(wind_towards_direction, bearing_to_farm)
    wind_to_farm = alignment <= WIND_TOWARDS_FARM_TOLERANCE_DEG
    return {
        "wind_direction": _format_wind_direction(wind_from),
        "wind_speed_kmh": round(float(wind_speed), 1) if wind_speed is not None else "",
        "wind_to_farm": wind_to_farm,
        "wind_to_farm_label": "Sim" if wind_to_farm else "Nao",
        "wind_alignment_deg": round(alignment, 1),
        "wind_bearing_to_farm_deg": round(bearing_to_farm, 0),
        "wind_source": wind_context.get("source", ""),
    }


def _inpe_row_detections(gdf, source_key: str, source: Dict) -> List[Dict]:
    detections: List[Dict] = []
    if gdf is None or gdf.empty:
        return detections
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        event_time = row.get("__timestamp_utc")
        props = {
            "id": str(row.get("id", "")),
            "data_hora_brasilia": format_datetime_brasilia(event_time),
            "data_hora_zulu": format_datetime_zulu(event_time),
            "municipio": str(row.get("municipio", "")),
            "estado": str(row.get("estado", "")),
            "bioma": str(row.get("bioma", "")),
            "frp": str(row.get("frp", "")),
            "risco_fogo": str(row.get("risco_fogo", "")),
            "dias_sem_chuva": str(row.get("numero_dias_sem_chuva", "")),
            "precipitacao": str(row.get("precipitacao", "")),
        }
        detections.append(
            {
                "lon": float(geom.x),
                "lat": float(geom.y),
                "geometry_geojson": geom.__geo_interface__,
                "geometry_type": "Point",
                "source": source["name"],
                "source_key": source_key,
                "satellite": str(row.get("satelite") or source.get("satellite", "BDQueimadas")),
                "event_type": str(source.get("event_type", "Foco INPE Queimadas")),
                "alert_capable": bool(source.get("alert")),
                "distance_capable": bool(source.get("distance")),
                "priority": int(source.get("priority", 99)),
                "properties": props,
                "value": _safe_float(row.get("frp")),
                "detection_datetime": props["data_hora_brasilia"],
                "detection_datetime_zulu": props["data_hora_zulu"],
                "detection_period": props["data_hora_brasilia"],
            }
        )
    return detections


def _inpe_queimadas(source_key: str, source: Dict, roi_geojson: Dict, start: datetime, end: datetime, reference: datetime) -> Dict:
    roi_shape = shape(roi_geojson)
    frames: List[gpd.GeoDataFrame] = []
    errors: List[str] = []
    downloaded_days: List[str] = []

    for day in _hms_candidate_days(start, end):
        try:
            csv_path = _download_inpe_daily_csv(day)
            if not csv_path:
                errors.append(f"{day:%Y-%m-%d}: CSV nao encontrado")
                continue
            downloaded_days.append(day.strftime("%Y-%m-%d"))
            df = pd.read_csv(csv_path, low_memory=False)
            if df.empty:
                continue
            df.columns = [str(column).strip().lower() for column in df.columns]
            time_column = "data_hora_gmt" if "data_hora_gmt" in df.columns else "data"
            if time_column not in df.columns or "lat" not in df.columns or "lon" not in df.columns:
                errors.append(f"{day:%Y-%m-%d}: colunas esperadas ausentes")
                continue
            df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
            df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
            df["__timestamp_utc"] = pd.to_datetime(df[time_column], utc=True, errors="coerce")
            df = df.dropna(subset=["lat", "lon", "__timestamp_utc"]).copy()
            df = df[(df["__timestamp_utc"] >= pd.Timestamp(start)) & (df["__timestamp_utc"] <= pd.Timestamp(end))]
            if df.empty:
                continue
            gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs="EPSG:4326")
            gdf = gdf[gdf.geometry.intersects(roi_shape)].copy()
            if not gdf.empty:
                frames.append(gdf)
        except Exception as exc:
            errors.append(f"{day:%Y-%m-%d}: {exc}")

    if not frames:
        message = "Sem focos INPE/BDQueimadas na ROI e na janela de referencia."
        if errors:
            message = f"{message} Detalhes: {' | '.join(errors[:5])}"
        return _empty_result(source_key, source, reference, start, end, "ignorado", message)

    focos = pd.concat(frames, ignore_index=True)
    if "id" in focos.columns:
        focos = focos.drop_duplicates(subset=["id"]).copy()
    else:
        duplicate_columns = [column for column in ["lat", "lon", "__timestamp_utc", "satelite"] if column in focos.columns]
        focos = focos.drop_duplicates(subset=duplicate_columns).copy()
    focos = gpd.GeoDataFrame(focos, geometry="geometry", crs="EPSG:4326")
    focos = focos.sort_values("__timestamp_utc", ascending=False).copy()
    sample_limit = int(source.get("sample_limit", 10000))
    if len(focos) > sample_limit:
        focos = focos.head(sample_limit).copy()

    detections = _inpe_row_detections(focos, source_key, source)
    latest_time = focos["__timestamp_utc"].max() if "__timestamp_utc" in focos.columns else None
    render_columns = [
        column
        for column in [
            "id",
            "lat",
            "lon",
            "data_hora_gmt",
            "data",
            "satelite",
            "municipio",
            "estado",
            "bioma",
            "frp",
            "risco_fogo",
            "geometry",
        ]
        if column in focos.columns
    ]
    render_gdf = focos[render_columns].copy()
    for column in render_gdf.columns:
        if column != "geometry":
            render_gdf[column] = render_gdf[column].astype(str)

    layer = {
        "name": "INPE Queimadas | Focos detectados",
        "layer_type": "point_geojson",
        "geojson": render_gdf.to_json(),
        "fields": [column for column in ["satelite", "municipio", "estado", "bioma", "frp", "risco_fogo"] if column in render_gdf.columns],
        "source": source["name"],
        "indicator": source["label"],
        "image_datetime": format_datetime_brasilia(latest_time),
        "image_datetime_zulu": format_datetime_zulu(latest_time),
        "period": format_period_brasilia(start, end),
        "period_zulu": format_period_zulu(start, end),
        "composition": f"CSV diario BDQueimadas filtrado pela ROI ({', '.join(downloaded_days)})",
        "source_key": source_key,
        "color": "#22c55e",
        "show": True,
    }
    count = int(len(focos))
    message = f"{count} foco(s) INPE/BDQueimadas encontrado(s) e processado(s) para distancia."
    return {
        "source_key": source_key,
        "source": source,
        "layers": [layer],
        "points": detections,
        "count": count,
        "image_datetime": layer["image_datetime"],
        "image_datetime_zulu": layer["image_datetime_zulu"],
        "status": "plotado",
        "message": message,
        "log": _log(source_key, source, reference, start, end, count, "plotado", message),
    }


def _noaa_hms_smoke(source_key: str, source: Dict, roi_geojson: Dict, start: datetime, end: datetime, reference: datetime) -> Dict:
    roi_shape = shape(roi_geojson)
    roi_gdf = gpd.GeoDataFrame(geometry=[roi_shape], crs="EPSG:4326")
    errors: List[str] = []
    no_intersection_days: List[str] = []
    for day in _hms_candidate_days(start, end):
        try:
            zip_path = _download_hms_zip(day)
            if not zip_path:
                errors.append(f"{day:%Y-%m-%d}: arquivo nao encontrado")
                continue
            gdf = gpd.read_file(f"zip://{zip_path.as_posix()}")
            if gdf.empty:
                errors.append(f"{day:%Y-%m-%d}: arquivo vazio")
                continue
            if gdf.crs is None:
                gdf = gdf.set_crs("EPSG:4326")
            else:
                gdf = gdf.to_crs("EPSG:4326")
            gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
            clipped = gpd.clip(gdf, roi_gdf)
            if clipped.empty:
                no_intersection_days.append(day.strftime("%Y-%m-%d"))
                continue

            keep_columns = [column for column in ["Satellite", "Start", "End_", "Density", "geometry"] if column in clipped.columns]
            clipped = clipped[keep_columns].copy()
            for column in clipped.columns:
                if column == "geometry":
                    continue
                clipped[column] = clipped[column].astype(str)
            geojson = clipped.to_json()
            detection_datetime = f"Produto diario {day:%Y-%m-%d}"
            detections = _geometry_detections(
                clipped,
                source_key,
                {
                    **source,
                    "detection_datetime": detection_datetime,
                    "detection_datetime_zulu": detection_datetime,
                    "detection_period": f"Arquivo diario HMS {day:%Y-%m-%d}",
                },
                satellite=str(source.get("satellite", "NOAA HMS")),
                event_type=str(source.get("event_type", "Fumaca detectada")),
            )
            count = int(len(clipped))
            layer = {
                "name": "NOAA HMS Smoke",
                "geojson": geojson,
                "layer_type": "smoke_geojson",
                "fields": [column for column in keep_columns if column != "geometry"],
                "source": "NOAA HMS Smoke Polygons",
                "indicator": source["label"],
                "image_datetime": f"Produto diario {day:%Y-%m-%d}",
                "image_datetime_zulu": f"Produto diario {day:%Y-%m-%d}",
                "period": f"Arquivo diario HMS {day:%Y-%m-%d}",
                "period_zulu": f"Arquivo diario HMS {day:%Y-%m-%d}",
                "composition": "Poligonos de fumaca classificados por densidade",
                "source_key": source_key,
                "opacity": 0.38,
            }
            return {
                "source_key": source_key,
                "source": source,
                "layers": [layer],
                "points": detections,
                "count": count,
                "image_datetime": layer["image_datetime"],
                "status": "plotado",
                "message": f"{count} poligono(s) HMS Smoke na ROI.",
                "log": _log(source_key, source, reference, start, end, count, "plotado", f"HMS Smoke plotado com arquivo {day:%Y-%m-%d}."),
            }
        except Exception as exc:
            errors.append(f"{day:%Y-%m-%d}: {exc}")
    message = "NOAA HMS Smoke sem arquivo valido ou sem dado na janela."
    if no_intersection_days:
        message = f"HMS Smoke encontrado em {', '.join(no_intersection_days)}, mas sem poligonos intersectando a ROI."
    if errors:
        message = f"{message} {' | '.join(errors[:3])}"
    return _empty_result(source_key, source, reference, start, end, "ignorado", message)


def _smoke_aerosol_context(source_key: str, source: Dict, roi, start: datetime, end: datetime, reference: datetime) -> Dict:
    layer_specs = [
        {
            "collection": "COPERNICUS/S5P/NRTI/L3_AER_AI",
            "band": "absorbing_aerosol_index",
            "name": "GE | Sentinel-5P indice de aerossois",
            "source": "Sentinel-5P NRTI AER AI",
            "vis": {"min": -1, "max": 5, "palette": ["#1d4ed8", "#ffffff", "#facc15", "#f97316", "#7f1d1d"]},
            "composition": "Imagem mais proxima: indice de aerossois absorventes",
            "transform": None,
        },
        {
            "collection": "ECMWF/CAMS/NRT",
            "band": "particulate_matter_d_less_than_25_um_surface",
            "name": "GE | CAMS PM2.5 superficie",
            "source": "CAMS NRT",
            "vis": {"min": 0, "max": 80, "palette": ["#dbeafe", "#fde047", "#fb923c", "#dc2626", "#7f1d1d"]},
            "composition": "Imagem mais proxima: PM2.5 de superficie convertido para ug/m3",
            "transform": "kg_m3_to_ug_m3",
        },
    ]
    layers: List[Dict] = []
    total_count = 0
    messages: List[str] = []
    for spec in layer_specs:
        try:
            collection = ee.ImageCollection(str(spec["collection"])).filterBounds(roi).filterDate(start.isoformat(), end.isoformat())
            count = int(collection.size().getInfo())
            total_count += count
            if count == 0:
                messages.append(f"Sem imagens em {spec['source']}.")
                continue
            nearest = _nearest_image(collection, reference)
            image = nearest.select(str(spec["band"]))
            if spec.get("transform") == "kg_m3_to_ug_m3":
                image = image.multiply(1_000_000_000).rename("PM25_UG_M3")
            if roi:
                image = image.clip(roi)
            layers.append(
                {
                    "name": spec["name"],
                    "url": build_tile_url(image, spec["vis"]),
                    "opacity": 0.55,
                    "source": spec["source"],
                    "indicator": source["label"],
                    "image_datetime": _image_time(nearest),
                    "image_datetime_zulu": _image_time_zulu(nearest),
                    "period": f"Imagem mais proxima dentro das {_window_label(start, end)}",
                    "period_zulu": format_period_zulu(start, end),
                    "composition": spec["composition"],
                    "source_key": source_key,
                    "show": False,
                }
            )
        except Exception as exc:
            messages.append(f"{spec['source']}: {exc}")
    if not layers:
        return _empty_result(source_key, source, reference, start, end, "ignorado", " ".join(messages) or "Sem dados de fumaca/aerossois na janela.")
    return {
        "source_key": source_key,
        "source": source,
        "layers": layers,
        "points": [],
        "count": total_count,
        "image_datetime": layers[0].get("image_datetime", ""),
        "image_datetime_zulu": layers[0].get("image_datetime_zulu", ""),
        "status": "plotado",
        "message": f"{len(layers)} camada(s) de fumaca/aerossois plotada(s).",
        "log": _log(source_key, source, reference, start, end, total_count, "plotado", f"{len(layers)} camada(s) de fumaca/aerossois plotada(s)."),
    }


def _goes_fdcf(source_key: str, source: Dict, roi, start: datetime, end: datetime, reference: datetime) -> Dict:
    merged_collection = None
    total_count = 0
    used_collections: List[str] = []
    last_messages: List[str] = []
    for collection_id in source.get("collections", []):
        collection = ee.ImageCollection(str(collection_id)).filterBounds(roi).filterDate(start.isoformat(), end.isoformat())
        count = int(collection.size().getInfo())
        if count == 0:
            last_messages.append(f"Sem imagens em {collection_id}.")
            continue
        total_count += count
        used_collections.append(str(collection_id))
        merged_collection = collection if merged_collection is None else merged_collection.merge(collection)

    if merged_collection is None or total_count == 0:
        message = f"Sem imagens GOES FDCF nas {_window_label(start, end)}."
        if last_messages:
            message = f"{message} {' '.join(last_messages)}"
        return _empty_result(source_key, source, reference, start, end, "ignorado", message)

    band = str(source["band"])
    image = merged_collection.select(band).max().selfMask().rename(band)
    if roi:
        image = image.clip(roi)
    layer = {
        "name": f"GE | {source['name']}",
        "url": build_tile_url(image, source["vis"]),
        "opacity": float(source.get("opacity", 0.75)),
        "source": f"{source['name']} ({', '.join(used_collections)})",
        "indicator": source["label"],
        "image_datetime": _image_time(merged_collection.sort("system:time_start", False).first()),
        "image_datetime_zulu": _image_time_zulu(merged_collection.sort("system:time_start", False).first()),
        "period": format_period_brasilia(start, end),
        "period_zulu": format_period_zulu(start, end),
        "composition": f"{band} maximo nas {_window_label(start, end)}",
        "source_key": source_key,
    }
    sample_source = dict(source)
    sample_source.update(
        {
            "detection_datetime": layer["image_datetime"],
            "detection_datetime_zulu": layer["image_datetime_zulu"],
            "detection_period": layer["period"],
        }
    )
    points = _sample_hotspot_points(image, roi, source_key, sample_source)
    return {
        "source_key": source_key,
        "source": source,
        "layers": [layer],
        "points": points,
        "count": total_count,
        "image_datetime": layer["image_datetime"],
        "status": "plotado",
        "message": f"{total_count} imagem(ns) encontrada(s) em GOES FDCF; {len(points)} ponto(s) amostrado(s).",
        "log": _log(source_key, source, reference, start, end, total_count, "plotado", f"GOES FDCF acumulado na janela; {len(points)} ponto(s) amostrado(s)."),
    }


def _viirs_375(source_key: str, source: Dict, roi, start: datetime, end: datetime, reference: datetime) -> Dict:
    attempts = [
        ("VIIRS LANCE 375 m", source.get("collections", []), "frp"),
        ("VIIRS VNP14A1 375 m", [source.get("fallback_collection")], "MaxFRP"),
    ]
    last_log = None
    errors: List[str] = []
    first_layer_without_points: Optional[Dict] = None
    for source_label, collection_ids, band in attempts:
        collection_ids = [collection_id for collection_id in collection_ids if collection_id]
        if not collection_ids:
            continue
        try:
            collection = ee.ImageCollection(str(collection_ids[0]))
            for collection_id in collection_ids[1:]:
                collection = collection.merge(ee.ImageCollection(str(collection_id)))
            collection = collection.filterBounds(roi).filterDate(start.isoformat(), end.isoformat())
            count = int(collection.size().getInfo())
            if count == 0:
                last_log = _log(source_key, source, reference, start, end, count, "ignorado", f"Sem imagens em {source_label}.")
                continue

            sample_source = dict(source)
            sample_source["band"] = "VIIRS_FRP"
            image = collection.select(band).max().selfMask().rename("VIIRS_FRP")
            if roi:
                image = image.clip(roi)
            layer = {
                "name": f"GE | {source['name']}",
                "url": build_tile_url(image, source["vis"]),
                "opacity": float(source.get("opacity", 0.75)),
                "source": source_label,
                "indicator": source["label"],
                "image_datetime": _image_time(collection.sort("system:time_start", False).first()),
                "image_datetime_zulu": _image_time_zulu(collection.sort("system:time_start", False).first()),
                "period": format_period_brasilia(start, end),
                "period_zulu": format_period_zulu(start, end),
                "composition": f"{band} maximo nas {_window_label(start, end)}",
                "source_key": source_key,
            }
            sample_source.update(
                {
                    "detection_datetime": layer["image_datetime"],
                    "detection_datetime_zulu": layer["image_datetime_zulu"],
                    "detection_period": layer["period"],
                }
            )
            points = _sample_hotspot_points(image, roi, source_key, sample_source)
            result = {
                "source_key": source_key,
                "source": source,
                "layers": [layer],
                "points": points,
                "count": count,
                "image_datetime": layer["image_datetime"],
                "status": "plotado",
                "message": f"{count} imagem(ns) encontrada(s) em {source_label}; {len(points)} ponto(s) amostrado(s).",
                "log": _log(source_key, source, reference, start, end, count, "plotado", f"{source_label} plotado; {len(points)} ponto(s) amostrado(s)."),
            }
            if points:
                return result
            if first_layer_without_points is None:
                first_layer_without_points = result
            continue
        except Exception as exc:
            errors.append(f"{source_label}: {exc}")

    if first_layer_without_points is not None:
        first_layer_without_points["message"] = f"{first_layer_without_points['message']} Nenhum pixel positivo foi encontrado para gerar distancia."
        first_layer_without_points["log"]["mensagem"] = first_layer_without_points["message"]
        return first_layer_without_points

    message = f"Sem imagens VIIRS nas {_window_label(start, end)}."
    if errors:
        message = f"{message} Tentativas com erro controlado: {' | '.join(errors)}"
    result = _empty_result(source_key, source, reference, start, end, "ignorado", message)
    if last_log:
        result["log"] = last_log
        result["log"]["mensagem"] = message
    return result


def _nasa_gibs_hotspots(source_key: str, source: Dict, roi_geojson: Dict, start: datetime, end: datetime, reference: datetime) -> Dict:
    gibs_day = reference.date().isoformat()
    layers: List[Dict] = []
    for spec in source.get("gibs_layers", []):
        layers.append(
            {
                "name": spec.get("name", f"NASA GIBS | {spec.get('layer', '')}"),
                "layer_type": "wms",
                "url": NASA_GIBS_WMS_URL,
                "wms_layers": spec.get("layer"),
                "format": "image/png",
                "transparent": True,
                "version": "1.3.0",
                "time": gibs_day,
                "opacity": float(source.get("opacity", 0.95)),
                "source": spec.get("source", "NASA GIBS FIRMS"),
                "indicator": source["label"],
                "image_datetime": f"Produto diario GIBS {gibs_day}",
                "image_datetime_zulu": f"Produto diario GIBS {gibs_day}",
                "period": f"Camada diaria GIBS em {gibs_day}; pontos calculados em {format_period_brasilia(start, end)}",
                "period_zulu": f"Camada diaria GIBS em {gibs_day}; pontos calculados em {format_period_zulu(start, end)}",
                "composition": "NASA GIBS WMS FIRMS Thermal Anomalies",
                "source_key": source_key,
                "show": True,
            }
        )

    points: List[Dict] = []
    total_count = 0
    messages: List[str] = []
    try:
        roi = _roi_geometry(roi_geojson)
        for spec in source.get("point_sources", []):
            collection_ids = [collection_id for collection_id in spec.get("collections", []) if collection_id]
            if not collection_ids:
                continue
            collection = ee.ImageCollection(str(collection_ids[0]))
            for collection_id in collection_ids[1:]:
                collection = collection.merge(ee.ImageCollection(str(collection_id)))
            collection = collection.filterBounds(roi).filterDate(start.isoformat(), end.isoformat())
            count = int(collection.size().getInfo())
            total_count += count
            if count == 0:
                messages.append(f"Sem imagens em {', '.join(collection_ids)}.")
                continue

            band = str(spec["band"])
            renamed_band = str(spec.get("rename", band))
            image = collection.select(band).max().selfMask().rename(renamed_band)
            if roi:
                image = image.clip(roi)
            sample_source = dict(source)
            sample_source.update(
                {
                    "band": renamed_band,
                    "sample_scale": int(spec.get("sample_scale") or source.get("sample_scale") or 1000),
                    "sample_limit": int(spec.get("sample_limit") or source.get("sample_limit") or 5000),
                    "satellite": spec.get("satellite", source.get("satellite", source["name"])),
                    "event_type": spec.get("event_type", source.get("event_type", source["type"])),
                    "vis": spec.get("vis", source.get("vis", {})),
                    "detection_datetime": _image_time(collection.sort("system:time_start", False).first()),
                    "detection_datetime_zulu": _image_time_zulu(collection.sort("system:time_start", False).first()),
                    "detection_period": format_period_brasilia(start, end),
                }
            )
            sampled = _sample_hotspot_points(image, roi, source_key, sample_source)
            points.extend(sampled)
            messages.append(f"{count} imagem(ns) em {spec.get('satellite', 'NASA GIBS')}; {len(sampled)} ponto(s) amostrado(s).")
    except Exception as exc:
        messages.append(f"Pontos NASA GIBS nao amostrados via GEE: {exc}")

    status_count = total_count if total_count else len(layers)
    message = " ".join(messages) if messages else "Camadas NASA GIBS carregadas; sem pontos amostrados nesta janela."
    if layers and not points:
        message = f"{message} A camada visual foi adicionada, mas nao houve ponto positivo para distancia."
    return {
        "source_key": source_key,
        "source": source,
        "layers": layers,
        "points": points,
        "count": status_count,
        "image_datetime": f"Produto diario GIBS {gibs_day}",
        "status": "plotado" if layers else "ignorado",
        "message": message,
        "log": _log(source_key, source, reference, start, end, status_count, "plotado" if layers else "ignorado", message),
    }


def _nearest_image_layer(source_key: str, source: Dict, roi, start: datetime, end: datetime, reference: datetime, band_override: Optional[str] = None) -> Dict:
    last_log = None
    for collection_id in source.get("collections", [source.get("collection")]):
        collection = ee.ImageCollection(str(collection_id)).filterBounds(roi).filterDate(start.isoformat(), end.isoformat())
        count = int(collection.size().getInfo())
        if count == 0:
            last_log = _log(source_key, source, reference, start, end, count, "ignorado", f"Sem imagens em {collection_id}.")
            continue
        nearest = _nearest_image(collection, reference)
        band = band_override or source.get("band")
        image = nearest.select(str(band)).selfMask() if band else nearest
        if roi:
            image = image.clip(roi)
        layer = {
            "name": f"GE | {source['name']}",
            "url": build_tile_url(image, source["vis"]),
            "opacity": float(source.get("opacity", 0.75)),
            "source": source["name"],
            "indicator": source["label"],
            "image_datetime": _image_time(nearest),
            "image_datetime_zulu": _image_time_zulu(nearest),
            "period": f"Imagem mais proxima dentro das {_window_label(start, end)}",
            "period_zulu": format_period_zulu(start, end),
            "composition": "Imagem mais proxima temporalmente da referencia",
            "source_key": source_key,
        }
        sample_source = dict(source)
        sample_source.update(
            {
                "detection_datetime": layer["image_datetime"],
                "detection_datetime_zulu": layer["image_datetime_zulu"],
                "detection_period": layer["period"],
            }
        )
        points = _sample_hotspot_points(image, roi, source_key, sample_source) if source.get("distance") else []
        return {
            "source_key": source_key,
            "source": source,
            "layers": [layer],
            "points": points,
            "count": count,
            "image_datetime": layer["image_datetime"],
            "status": "plotado",
            "message": f"{count} imagem(ns) encontrada(s) em {collection_id}.",
            "log": _log(source_key, source, reference, start, end, count, "plotado", f"Camada plotada de {collection_id}."),
        }
    result = _empty_result(source_key, source, reference, start, end, "ignorado", f"Sem imagens nas {_window_label(start, end)}.")
    if last_log:
        result["log"] = last_log
    return result


def _sentinel2_ndvi(source_key: str, source: Dict, roi, start: datetime, end: datetime, reference: datetime) -> Dict:
    collection = (
        ee.ImageCollection(str(source["collection"]))
        .filterBounds(roi)
        .filterDate(start.isoformat(), end.isoformat())
        .filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", 50))
    )
    count = int(collection.size().getInfo())
    if count == 0:
        return _empty_result(source_key, source, reference, start, end, "ignorado", f"Sem Sentinel-2 com nuvens <= 50% nas {_window_label(start, end)}.")
    image = collection.median().normalizedDifference(["B8", "B4"]).rename("NDVI").clip(roi)
    layer = {
        "name": "GE | Sentinel-2 NDVI",
        "url": build_tile_url(image, {"min": -0.2, "max": 0.8, "palette": ["#7f1d1d", "#f59e0b", "#22c55e"]}),
        "opacity": float(source.get("opacity", 0.55)),
        "source": source["name"],
        "indicator": source["label"],
        "image_datetime": _image_time(collection.sort("system:time_start", False).first()),
        "image_datetime_zulu": _image_time_zulu(collection.sort("system:time_start", False).first()),
        "period": format_period_brasilia(start, end),
        "period_zulu": format_period_zulu(start, end),
        "composition": f"NDVI mediano nas {_window_label(start, end)}",
        "source_key": source_key,
    }
    return {"source_key": source_key, "source": source, "layers": [layer], "points": [], "count": count, "image_datetime": layer["image_datetime"], "status": "plotado", "message": f"{count} imagem(ns) Sentinel-2.", "log": _log(source_key, source, reference, start, end, count, "plotado", "NDVI plotado.")}


def _landsat_thermal(source_key: str, source: Dict, roi, start: datetime, end: datetime, reference: datetime) -> Dict:
    collection = (
        ee.ImageCollection(str(source["collection"]))
        .filterBounds(roi)
        .filterDate(start.isoformat(), end.isoformat())
        .filter(ee.Filter.lte("CLOUD_COVER", 50))
    )
    count = int(collection.size().getInfo())
    if count == 0:
        return _empty_result(source_key, source, reference, start, end, "ignorado", f"Sem Landsat termal com baixa nebulosidade nas {_window_label(start, end)}.")
    nearest = _nearest_image(collection, reference)
    image = nearest.select("ST_B10").multiply(0.00341802).add(149.0).subtract(273.15).clip(roi)
    layer = {
        "name": "GE | Landsat temperatura de superficie",
        "url": build_tile_url(image, {"min": 15, "max": 55, "palette": ["#313695", "#ffffbf", "#a50026"]}),
        "opacity": float(source.get("opacity", 0.50)),
        "source": source["name"],
        "indicator": source["label"],
        "image_datetime": _image_time(nearest),
        "image_datetime_zulu": _image_time_zulu(nearest),
        "period": f"Imagem mais proxima dentro das {_window_label(start, end)}",
        "period_zulu": format_period_zulu(start, end),
        "composition": "ST_B10 convertido para Celsius",
        "source_key": source_key,
    }
    return {"source_key": source_key, "source": source, "layers": [layer], "points": [], "count": count, "image_datetime": layer["image_datetime"], "status": "plotado", "message": f"{count} imagem(ns) Landsat.", "log": _log(source_key, source, reference, start, end, count, "plotado", "Landsat termal plotado.")}


def _era5_temperature(source_key: str, source: Dict, roi, start: datetime, end: datetime, reference: datetime) -> Dict:
    collection = ee.ImageCollection(str(source["collection"])).filterBounds(roi).filterDate(start.date().isoformat(), end.date().isoformat())
    count = int(collection.size().getInfo())
    if count == 0:
        return _empty_result(source_key, source, reference, start, end, "ignorado", f"Sem ERA5 Land nas {_window_label(start, end)}.")
    image = collection.select("temperature_2m").mean().subtract(273.15).clip(roi)
    layer = {
        "name": "GE | ERA5 Land temperatura",
        "url": build_tile_url(image, {"min": 10, "max": 45, "palette": ["#313695", "#ffffbf", "#a50026"]}),
        "opacity": float(source.get("opacity", 0.45)),
        "source": source["name"],
        "indicator": source["label"],
        "image_datetime": _image_time(collection.sort("system:time_start", False).first()),
        "image_datetime_zulu": _image_time_zulu(collection.sort("system:time_start", False).first()),
        "period": format_period_brasilia(start, end),
        "period_zulu": format_period_zulu(start, end),
        "composition": f"Temperatura media 2m nas {_window_label(start, end)}",
        "source_key": source_key,
    }
    return {"source_key": source_key, "source": source, "layers": [layer], "points": [], "count": count, "image_datetime": layer["image_datetime"], "status": "plotado", "message": f"{count} imagem(ns) ERA5.", "log": _log(source_key, source, reference, start, end, count, "plotado", "ERA5 temperatura plotada.")}


def _source_window_hours(source: Dict, active_fire_window_hours: Optional[float] = None) -> float:
    default_window = float(source.get("window_hours", float(source.get("window_days", 1)) * 24.0))
    if active_fire_window_hours is not None and abs(default_window - float(ACTIVE_FIRE_WINDOW_HOURS)) < 0.01:
        return float(active_fire_window_hours)
    return default_window


def fetch_source_data(
    source_name: str,
    roi_geojson: Dict,
    reference_date: str | datetime | Dict,
    active_fire_window_hours: Optional[float] = None,
) -> Dict:
    source = FIRE_DATA_SOURCES[source_name]
    window_hours = _source_window_hours(source, active_fire_window_hours)
    start, end, reference = get_temporal_window(reference_date, window_hours)
    if source.get("query") == "unconfigured":
        return _empty_result(source_name, source, reference, start, end, "ignorado", "Fonte registrada, mas sem colecao GEE configurada no sistema atual.")

    try:
        query = source.get("query")
        if query == "noaa_hms_smoke":
            return _noaa_hms_smoke(source_name, source, roi_geojson, start, end, reference)
        if query == "inpe_queimadas":
            return _inpe_queimadas(source_name, source, roi_geojson, start, end, reference)
        if query == "nasa_gibs_hotspots":
            return _nasa_gibs_hotspots(source_name, source, roi_geojson, start, end, reference)
        roi = _roi_geometry(roi_geojson)
        if query in {"goes_visual", "goes_thermal"}:
            return _nearest_image_layer(source_name, source, roi, start, end, reference)
        if query == "goes_fdcf":
            return _goes_fdcf(source_name, source, roi, start, end, reference)
        if query == "viirs_375":
            return _viirs_375(source_name, source, roi, start, end, reference)
        if query == "image_collection_max":
            return _image_collection_max(source_name, source, roi, start, end, reference)
        if query == "sentinel2_ndvi":
            return _sentinel2_ndvi(source_name, source, roi, start, end, reference)
        if query == "landsat_thermal":
            return _landsat_thermal(source_name, source, roi, start, end, reference)
        if query == "era5_temperature":
            return _era5_temperature(source_name, source, roi, start, end, reference)
        if query == "smoke_aerosol_context":
            return _smoke_aerosol_context(source_name, source, roi, start, end, reference)
        return _empty_result(source_name, source, reference, start, end, "ignorado", "Tipo de consulta nao implementado.")
    except Exception as exc:
        return _empty_result(source_name, source, reference, start, end, "erro_controlado", str(exc))


def selected_source_keys(indicators: List[str]) -> List[str]:
    labels = source_labels()
    keys = []
    for indicator in indicators:
        key = labels.get(indicator)
        if key and key not in keys:
            keys.append(key)
    return keys


def fetch_selected_sources(
    indicators: List[str],
    roi_geojson: Dict,
    reference_date: str | datetime | Dict,
    active_fire_window_hours: Optional[float] = None,
) -> Dict:
    source_keys = selected_source_keys(indicators)
    ok, message = initialize_earth_engine()
    if not ok or ee is None:
        results = []
        skipped = []
        for source_key in source_keys:
            source = FIRE_DATA_SOURCES[source_key]
            if not source.get("requires_ee", True):
                results.append(fetch_source_data(source_key, roi_geojson, reference_date, active_fire_window_hours))
            else:
                skipped.append(str(source["name"]))
        log = [{"consulta": format_datetime_brasilia(datetime.now(timezone.utc)), "fonte": "Earth Engine", "status": "erro_controlado", "mensagem": f"{message} Fontes GEE ignoradas: {', '.join(skipped)}", "quantidade": 0}]
        if not results:
            return {"layers": [], "points": [], "counts": {}, "logs": log, "results": [], "image_rows": []}
    else:
        results = []
        log = []

    for source_key in source_keys:
        if any(result.get("source_key") == source_key for result in results):
            continue
        if (not ok or ee is None) and FIRE_DATA_SOURCES[source_key].get("requires_ee", True):
            continue
        results.append(fetch_source_data(source_key, roi_geojson, reference_date, active_fire_window_hours))

    layers: List[Dict] = []
    points: List[Dict] = []
    counts: Dict[str, int] = {}
    logs: List[Dict] = []
    image_rows: List[Dict] = []
    for result in results:
        source = result["source"]
        layers.extend(result.get("layers", []))
        if source.get("distance"):
            points.extend(result.get("points", []))
        counts[str(result["source_key"])] = int(result.get("count", 0))
        logs.append(result["log"])
        for layer in result.get("layers", []):
            image_rows.append(
                {
                    "Camada": layer.get("name", source["name"]),
                    "Fonte": layer.get("source", source["name"]),
                    "Data/hora Brasilia": layer.get("image_datetime") or "Sem data retornada",
                    "Data/hora Zulu": layer.get("image_datetime_zulu") or "Sem data retornada",
                    "Periodo usado Brasilia": layer.get("period", ""),
                    "Periodo usado Zulu": layer.get("period_zulu", ""),
                    "Como foi plotado": layer.get("composition", ""),
                }
            )
    logs = log + logs
    return {"layers": layers, "points": points, "counts": counts, "logs": logs, "results": results, "image_rows": image_rows}


def compute_hotspot_distances(
    hotspot_points: List[Dict],
    farms_gdf,
    selected_companies: List[str],
    limit: int = 30,
    max_distance_km: float = 30.0,
    wind_context: Optional[Dict] = None,
) -> List[Dict]:
    if not hotspot_points or farms_gdf is None:
        return []
    selected = set(selected_companies or [])
    farms = farms_gdf.copy()
    if selected:
        farms = farms[farms["EMPRESA"].astype(str).str.strip().isin(selected)].copy()
    if "__geometry_original__" in farms.columns:
        farms["geometry"] = farms["__geometry_original__"]
    farms = farms[farms.geometry.notna() & ~farms.geometry.is_empty].copy()
    if farms.empty:
        return []
    farms_metric = farms.to_crs("EPSG:3857")
    detection_geometries = []
    detection_records = []
    for detection in hotspot_points:
        if not detection.get("distance_capable"):
            continue
        try:
            if detection.get("geometry_geojson"):
                geom = shape(detection["geometry_geojson"])
            else:
                geom = Point(float(detection["lon"]), float(detection["lat"]))
        except Exception:
            continue
        if geom is None or geom.is_empty:
            continue
        detection_geometries.append(geom)
        detection_records.append(detection)
    if not detection_geometries:
        return []
    detection_gdf = gpd.GeoDataFrame(
        detection_records,
        geometry=detection_geometries,
        crs="EPSG:4326",
    ).to_crs("EPSG:3857")

    rows = []
    detection_gdf_wgs84 = detection_gdf.to_crs("EPSG:4326")
    for point_idx, point_row in detection_gdf.iterrows():
        point_data = detection_records[point_idx]
        distances = farms_metric.geometry.distance(point_row.geometry)
        nearest_idx = distances.idxmin()
        distance_km = float(distances.loc[nearest_idx]) / 1000
        farm = farms.loc[nearest_idx]
        uf = str(farm.get("UF", "")).strip().upper()
        table_distance = min(float(max_distance_km), table_distance_for_uf(uf))
        if distance_km > table_distance:
            continue
        alert_distance = alert_distance_for_uf(uf)
        alert_capable = bool(point_data.get("alert_capable"))
        focus_geom = detection_gdf_wgs84.loc[point_idx].geometry
        focus_point = focus_geom if focus_geom.geom_type == "Point" else focus_geom.representative_point()
        nearest_on_farm = nearest_points(point_row.geometry, farms_metric.loc[nearest_idx].geometry)[1]
        farm_lon, farm_lat = WEB_TO_WGS84.transform(float(nearest_on_farm.x), float(nearest_on_farm.y))
        wind = _wind_to_farm_analysis(float(focus_point.x), float(focus_point.y), farm_lon, farm_lat, wind_context)
        wind_alert = bool(wind.get("wind_to_farm")) and distance_km <= 5.0
        rows.append(
            {
                "empresa": str(farm.get("EMPRESA", "")),
                "fazenda": str(farm.get("FAZENDA", "")),
                "municipio": str(farm.get("MUNICIPIO", "")),
                "uf": uf,
                "distancia_km": round(distance_km, 2),
                "distancia_alerta_km": alert_distance,
                "distancia_tabela_km": table_distance,
                "alerta_sonoro": alert_capable and distance_km <= alert_distance,
                "vento_direcao": wind.get("wind_direction", ""),
                "vento_velocidade_kmh": wind.get("wind_speed_kmh", ""),
                "vento_para_fazenda": wind.get("wind_to_farm_label", "Sem dados"),
                "vento_alinhamento_graus": wind.get("wind_alignment_deg", ""),
                "rumo_foco_fazenda_graus": wind.get("wind_bearing_to_farm_deg", ""),
                "fonte_vento": wind.get("wind_source", ""),
                "alerta_vento": wind_alert,
                "data_hora_deteccao": str(point_data.get("detection_datetime", "")),
                "data_hora_deteccao_zulu": str(point_data.get("detection_datetime_zulu", "")),
                "periodo_deteccao": str(point_data.get("detection_period", "")),
                "satelite": str(point_data.get("satellite", "")),
                "tipo": str(point_data.get("event_type", "")),
                "fonte": str(point_data.get("source", "")),
                "source_key": str(point_data.get("source_key", "")),
                "priority": int(point_data.get("priority", 99)),
                "latitude_foco": round(float(focus_point.y), 6),
                "longitude_foco": round(float(focus_point.x), 6),
                "geometria_deteccao": str(point_data.get("geometry_type", "point")),
            }
        )
    sorted_rows = sorted(rows, key=lambda item: (item["distancia_km"], item.get("priority", 99)))
    balanced_rows: List[Dict] = []
    seen_ids = set()
    per_source_count: Dict[str, int] = {}
    per_source_limit = 5
    for row in sorted_rows:
        key = row.get("source_key") or row.get("fonte") or row.get("satelite")
        if per_source_count.get(key, 0) >= per_source_limit:
            continue
        row_id = (
            row.get("source_key"),
            row.get("latitude_foco"),
            row.get("longitude_foco"),
            row.get("empresa"),
            row.get("fazenda"),
        )
        balanced_rows.append(row)
        seen_ids.add(row_id)
        per_source_count[key] = per_source_count.get(key, 0) + 1
        if len(balanced_rows) >= limit:
            return sorted(balanced_rows, key=lambda item: (item["distancia_km"], item.get("priority", 99)))
    for row in sorted_rows:
        row_id = (
            row.get("source_key"),
            row.get("latitude_foco"),
            row.get("longitude_foco"),
            row.get("empresa"),
            row.get("fazenda"),
        )
        if row_id in seen_ids:
            continue
        balanced_rows.append(row)
        if len(balanced_rows) >= limit:
            break
    return sorted(balanced_rows, key=lambda item: (item["distancia_km"], item.get("priority", 99)))


def classify_alert_level(distance_rows: List[Dict], risk_class: str = "") -> str:
    if any(row.get("alerta_sonoro") and row.get("priority", 99) <= 3 for row in distance_rows):
        return "Alerta alto"
    if any(row.get("alerta_vento") for row in distance_rows):
        return "Alerta medio - vento direcionado"
    if any(row.get("distancia_km", 999) <= row.get("distancia_alerta_km", 0) for row in distance_rows):
        return "Alerta medio"
    if str(risk_class).lower() in {"alto", "muito alto"}:
        return "Alerta baixo"
    return "Sem alerta"
