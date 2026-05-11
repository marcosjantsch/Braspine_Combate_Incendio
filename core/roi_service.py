# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict, List, Optional

from pyproj import Geod
from shapely.geometry import mapping, box

GEOD = Geod(ellps="WGS84")
DEFAULT_ROI_BUFFER_KM = 30.0


def selected_company_farms(gdf, selected_companies: List[str]):
    selected = {str(company).strip() for company in selected_companies or [] if str(company).strip()}
    if gdf is None or gdf.empty or not selected:
        return gdf.iloc[0:0].copy() if gdf is not None else None
    return gdf[gdf["EMPRESA"].astype(str).str.strip().isin(selected)].copy()


def _use_original_geometry(gdf):
    farms = gdf.copy()
    if "__geometry_original__" in farms.columns:
        farms["geometry"] = farms["__geometry_original__"]
    return farms[farms.geometry.notna() & ~farms.geometry.is_empty].copy()


def _buffer_bounds(west: float, south: float, east: float, north: float, buffer_km: float):
    buffer_m = float(buffer_km) * 1000.0
    center_lon = (west + east) / 2.0
    center_lat = (south + north) / 2.0
    buffered_west, _, _ = GEOD.fwd(west, center_lat, 270, buffer_m)
    buffered_east, _, _ = GEOD.fwd(east, center_lat, 90, buffer_m)
    _, buffered_south, _ = GEOD.fwd(center_lon, south, 180, buffer_m)
    _, buffered_north, _ = GEOD.fwd(center_lon, north, 0, buffer_m)
    return float(buffered_west), float(buffered_south), float(buffered_east), float(buffered_north)


def _dimensions_km(bounds: List[List[float]]) -> tuple[float, float]:
    south, west = bounds[0]
    north, east = bounds[1]
    center_lat = (south + north) / 2.0
    center_lon = (west + east) / 2.0
    width_km = abs(GEOD.inv(west, center_lat, east, center_lat)[2]) / 1000.0
    height_km = abs(GEOD.inv(center_lon, south, center_lon, north)[2]) / 1000.0
    return width_km, height_km


def build_project_roi(gdf, selected_companies: List[str], buffer_km: float = DEFAULT_ROI_BUFFER_KM) -> Dict:
    farms = selected_company_farms(gdf, selected_companies)
    selected_count = len({str(company).strip() for company in selected_companies or [] if str(company).strip()})
    invalid_status = (
        "A empresa selecionada nao possui geometria valida para ROI."
        if selected_count == 1
        else "As empresas selecionadas nao possuem geometria valida para ROI."
    )
    roi_subject = "pela empresa selecionada" if selected_count == 1 else "pelas empresas selecionadas"
    if farms is None or farms.empty:
        return {
            "ok": False,
            "geojson": None,
            "bounds": None,
            "width_km": 0.0,
            "height_km": 0.0,
            "status": "Selecione pelo menos uma empresa para calcular a ROI.",
        }

    farms = _use_original_geometry(farms)
    if farms.empty:
        return {
            "ok": False,
            "geojson": None,
            "bounds": None,
            "width_km": 0.0,
            "height_km": 0.0,
            "status": invalid_status,
        }

    west, south, east, north = [float(value) for value in farms.total_bounds]
    west, south, east, north = _buffer_bounds(west, south, east, north, buffer_km)
    bounds = [[float(south), float(west)], [float(north), float(east)]]
    width_km, height_km = _dimensions_km(bounds)
    return {
        "ok": True,
        "geojson": mapping(box(west, south, east, north)),
        "bounds": bounds,
        "width_km": width_km,
        "height_km": height_km,
        "buffer_km": float(buffer_km),
        "status": f"ROI calculada {roi_subject} com buffer de {buffer_km:.0f} km: {width_km:.1f} x {height_km:.1f} km.",
    }


def session_roi(session_state) -> Optional[Dict]:
    return session_state.get("roi_ee") or session_state.get("gee_roi")
