# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from core.time_context import format_datetime_brasilia
from services.gee_service import ee, initialize_earth_engine


GOES_RGB_VIS = {"bands": ["CMI_C02", "CMI_C03", "CMI_C01"], "min": 0.0, "max": 0.8, "gamma": 1.25}
GOES_THERMAL_VIS = {"bands": ["CMI_C13"], "min": 190, "max": 330, "palette": ["#7f00ff", "#004cff", "#00ffff", "#ffff00", "#ff0000"]}
GOES_HOTSPOT_VIS = {"min": 0, "max": 400, "palette": ["#ffff00", "#ff9900", "#ff0000", "#ff00ff"]}


def _roi_geometry(roi_geojson: Optional[Dict]):
    return ee.Geometry(roi_geojson) if roi_geojson else None


def _reference_datetime(reference_datetime: Optional[str] = None) -> datetime:
    if not reference_datetime:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(reference_datetime)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _nearest_image(
    collection_id: str,
    roi_geojson: Optional[Dict],
    reference_datetime: Optional[str] = None,
):
    reference = _reference_datetime(reference_datetime)
    reference_ms = int(reference.timestamp() * 1000)
    roi = _roi_geometry(roi_geojson)

    for hours in (24,):
        start = reference - timedelta(hours=hours)
        end = reference
        collection = ee.ImageCollection(collection_id).filterDate(start.isoformat(), end.isoformat())
        if roi:
            collection = collection.filterBounds(roi)
        if collection.size().getInfo() == 0:
            continue

        def set_time_delta(image):
            delta = ee.Number(image.get("system:time_start")).subtract(reference_ms).abs()
            return image.set("time_delta_ms", delta).set("search_window_hours", hours)

        return collection.map(set_time_delta).sort("time_delta_ms").first()
    return None


def image_time_iso(image) -> str:
    millis = image.get("system:time_start").getInfo()
    if not millis:
        return ""
    return format_datetime_brasilia(datetime.fromtimestamp(millis / 1000, tz=timezone.utc))


def get_latest_goes(
    roi_geojson: Optional[Dict],
    prefer_goes19: bool = True,
    reference_datetime: Optional[str] = None,
) -> Dict:
    ok, message = initialize_earth_engine()
    if not ok or ee is None:
        return {"goes_image": None, "goes_datetime": "", "hotspot_image": None, "status": message}

    goes_ids = ["NOAA/GOES/19/MCMIPF", "NOAA/GOES/16/MCMIPF"] if prefer_goes19 else ["NOAA/GOES/16/MCMIPF", "NOAA/GOES/19/MCMIPF"]
    fdcf_ids = ["NOAA/GOES/19/FDCF", "NOAA/GOES/16/FDCF"] if prefer_goes19 else ["NOAA/GOES/16/FDCF", "NOAA/GOES/19/FDCF"]

    last_error = ""
    for goes_id, fdcf_id in zip(goes_ids, fdcf_ids):
        try:
            goes_image = _nearest_image(goes_id, roi_geojson, reference_datetime=reference_datetime)
            if goes_image is None:
                last_error = f"Nenhuma imagem proxima encontrada em {goes_id} nas ultimas 24 horas da referencia."
                continue
            goes_datetime = image_time_iso(goes_image)
            hotspot_source = _nearest_image(fdcf_id, roi_geojson, reference_datetime=reference_datetime)
            hotspot_image = hotspot_source.select("Power").selfMask() if hotspot_source is not None else None
            if roi_geojson:
                geom = _roi_geometry(roi_geojson)
                goes_image = goes_image.clip(geom)
                if hotspot_image is not None:
                    hotspot_image = hotspot_image.clip(geom)
            return {
                "goes_image": goes_image,
                "goes_datetime": goes_datetime,
                "hotspot_image": hotspot_image,
                "status": f"GOES carregado de {goes_id}; imagem mais proxima: {goes_datetime}.",
            }
        except Exception as exc:
            last_error = str(exc)
    return {"goes_image": None, "goes_datetime": "", "hotspot_image": None, "status": last_error or "GOES indisponível."}
