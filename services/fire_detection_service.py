# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Tuple

import geopandas as gpd
from shapely.geometry import Point

from core.alert_rules import alert_distance_for_uf
from services.gee_service import ee, initialize_earth_engine
from services.goes_service import get_latest_goes


def _reference_date(reference_iso: str | None = None) -> date:
    if not reference_iso:
        return date.today()
    try:
        parsed = datetime.fromisoformat(reference_iso)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.date()
    except Exception:
        return date.today()


def _roi_geometry(roi_geojson: Dict | None):
    return ee.Geometry(roi_geojson) if roi_geojson else None


SOURCE_METADATA = {
    "MODIS FIRMS": {"satellite": "MODIS", "event_type": "Foco de calor"},
    "VIIRS": {"satellite": "VIIRS", "event_type": "Hotspot"},
    "MODIS anomalia": {"satellite": "MODIS Terra", "event_type": "Anomalia termica"},
    "GOES": {"satellite": "GOES", "event_type": "Hotspot GOES"},
}


def _count_and_sample(image, roi, scale: int, band_name: str, source: str) -> Tuple[int, List[Dict]]:
    masked = image.selfMask().rename(band_name)
    count_info = masked.reduceRegion(
        reducer=ee.Reducer.count(),
        geometry=roi,
        scale=scale,
        maxPixels=1_000_000_000,
        bestEffort=True,
    ).getInfo()
    count = int(count_info.get(band_name) or 0)
    features = (
        masked.sample(region=roi, scale=scale, numPixels=300, geometries=True)
        .limit(300)
        .getInfo()
        .get("features", [])
    )
    points = []
    for feature in features:
        coords = feature.get("geometry", {}).get("coordinates")
        if not coords or len(coords) < 2:
            continue
        metadata = SOURCE_METADATA.get(source, {"satellite": source, "event_type": "Foco"})
        points.append(
            {
                "lon": float(coords[0]),
                "lat": float(coords[1]),
                "source": source,
                "satellite": metadata["satellite"],
                "event_type": metadata["event_type"],
            }
        )
    return count, points


def build_fire_detection_summary(roi_geojson: Dict, reference_iso: str | None = None, indicators: List[str] | None = None) -> Dict:
    ok, message = initialize_earth_engine()
    if not ok or ee is None:
        return {"ok": False, "status": message, "counts": {}, "points": []}

    selected = set(indicators or [])
    roi = _roi_geometry(roi_geojson)
    end = _reference_date(reference_iso)
    start_recent = end - timedelta(days=30)
    counts = {
        "modis_firms": 0,
        "viirs_hotspots": 0,
        "modis_thermal": 0,
        "goes_hotspots": 0,
    }
    points: List[Dict] = []
    statuses = []

    if "FIRMS MODIS" in selected:
        try:
            firms = (
                ee.ImageCollection("FIRMS")
                .filterDate(start_recent.isoformat(), end.isoformat())
                .filterBounds(roi)
                .select("confidence")
                .max()
            )
            counts["modis_firms"], sampled = _count_and_sample(firms, roi, 1000, "firms", "MODIS FIRMS")
            points.extend(sampled)
        except Exception as exc:
            statuses.append(f"FIRMS indisponivel: {exc}")

    if "VIIRS 375 m" in selected:
        try:
            viirs = (
                ee.ImageCollection("NASA/VIIRS/002/VNP14A1")
                .filterDate(start_recent.isoformat(), end.isoformat())
                .filterBounds(roi)
                .select("MaxFRP")
                .max()
            )
            counts["viirs_hotspots"], sampled = _count_and_sample(viirs, roi, 375, "viirs", "VIIRS")
            points.extend(sampled)
        except Exception as exc:
            statuses.append(f"VIIRS indisponivel: {exc}")

    if "MODIS Terra FireMask" in selected:
        try:
            modis_thermal = (
                ee.ImageCollection("MODIS/061/MOD14A1")
                .filterDate(start_recent.isoformat(), end.isoformat())
                .filterBounds(roi)
                .select("MaxFRP")
                .max()
            )
            counts["modis_thermal"], sampled = _count_and_sample(modis_thermal, roi, 1000, "modis_thermal", "MODIS anomalia")
            points.extend(sampled)
        except Exception as exc:
            statuses.append(f"MODIS anomalia indisponivel: {exc}")

    if {"GOES-16 Hot Spot", "GOES hotspots recentes", "GOES visual meteorologico", "GOES temperatura de brilho"}.intersection(selected):
        try:
            goes = get_latest_goes(roi_geojson, reference_datetime=reference_iso)
            hotspot = goes.get("hotspot_image")
            if hotspot is not None:
                counts["goes_hotspots"], sampled = _count_and_sample(hotspot, roi, 2000, "goes", "GOES")
                points.extend(sampled)
            if goes.get("goes_datetime"):
                statuses.append(f"GOES proximo: {goes['goes_datetime']}")
        except Exception as exc:
            statuses.append(f"GOES hotspot indisponivel: {exc}")

    return {
        "ok": True,
        "counts": counts,
        "points": points,
        "status": " | ".join(statuses) if statuses else "Resumo de focos gerado.",
    }


def nearest_farms_to_hotspots(gdf, selected_companies: List[str], points: List[Dict], limit: int = 30) -> List[Dict]:
    if not points:
        return []

    selected = set(selected_companies or [])
    farms = gdf.copy()
    if selected:
        farms = farms[farms["EMPRESA"].astype(str).str.strip().isin(selected)].copy()
    if farms.empty:
        return []

    farms = farms[farms.geometry.notna() & ~farms.geometry.is_empty].copy()
    if farms.empty:
        return []

    farms_metric = farms.to_crs("EPSG:3857")
    point_gdf = gpd.GeoDataFrame(
        points,
        geometry=[Point(point["lon"], point["lat"]) for point in points],
        crs="EPSG:4326",
    )
    points_metric = point_gdf.to_crs("EPSG:3857")
    rows = []
    for point_idx, point_row in points_metric.iterrows():
        distances = farms_metric.geometry.distance(point_row.geometry)
        nearest_farm_idx = distances.idxmin()
        distance_km = float(distances.loc[nearest_farm_idx]) / 1000
        original_farm = farms.loc[nearest_farm_idx]
        original_point = point_gdf.loc[point_idx]
        uf = str(original_farm.get("UF", "")).strip().upper()
        alert_distance_km = alert_distance_for_uf(uf)
        rows.append(
            {
                "empresa": str(original_farm.get("EMPRESA", "")),
                "fazenda": str(original_farm.get("FAZENDA", "")),
                "municipio": str(original_farm.get("MUNICIPIO", "")),
                "uf": uf,
                "distancia_km": round(distance_km, 2),
                "distancia_alerta_km": alert_distance_km,
                "alerta_sonoro": distance_km <= alert_distance_km,
                "satelite": str(original_point.get("satellite", original_point.get("source", ""))),
                "tipo": str(original_point.get("event_type", original_point.get("source", ""))),
                "fonte": str(original_point.get("source", "")),
                "latitude_foco": round(float(original_point.geometry.y), 6),
                "longitude_foco": round(float(original_point.geometry.x), 6),
            }
        )
    return sorted(rows, key=lambda item: item["distancia_km"])[:limit]
