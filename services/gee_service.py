# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import streamlit as st

from core.auth_service import load_auth_config
from core.config import AUTH_CONFIG_PATH, BASE_DIR, DEFAULT_EE_PROJECT, GEO_PATH, SATELLITE_OPTIONS
from core.time_context import format_datetime_brasilia, format_period_brasilia

try:
    import ee
except Exception:
    ee = None


def earth_engine_settings() -> Dict[str, str]:
    try:
        config = load_auth_config()
    except Exception:
        config = {}
    settings = config.get("earth_engine", {}) or {}
    return {
        "project": str(settings.get("project") or "").strip(),
        "service_account_email": str(settings.get("service_account_email") or "").strip(),
        "service_account_file": str(settings.get("service_account_file") or "").strip(),
    }


def earth_engine_project() -> str:
    settings = earth_engine_settings()
    return (
        os.getenv("EE_PROJECT")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCP_PROJECT")
        or os.getenv("GCLOUD_PROJECT")
        or settings.get("project")
        or DEFAULT_EE_PROJECT
        or ""
    ).strip()


def read_service_account_metadata(credentials_path: Path) -> Tuple[str, str]:
    try:
        data = json.loads(credentials_path.read_text(encoding="utf-8"))
    except Exception:
        return "", ""
    return str(data.get("client_email") or "").strip(), str(data.get("project_id") or "").strip()


def initialize_earth_engine() -> Tuple[bool, str]:
    if ee is None:
        return False, "Pacote earthengine-api não está instalado."

    settings = earth_engine_settings()
    project = earth_engine_project()
    service_account_file = (
        os.getenv("EE_CREDENTIALS_PATH")
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        or settings.get("service_account_file")
        or ""
    ).strip()
    service_account_email = (
        settings.get("service_account_email")
        or os.getenv("EE_SERVICE_ACCOUNT_EMAIL")
        or os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL")
        or os.getenv("EE_SERVICE_ACCOUNT")
        or ""
    ).strip()
    if service_account_file and Path(service_account_file).exists():
        file_email, file_project = read_service_account_metadata(Path(service_account_file))
        service_account_email = service_account_email or file_email
        project = project or file_project

    try:
        if service_account_file and Path(service_account_file).exists() and service_account_email:
            credentials = ee.ServiceAccountCredentials(service_account_email, service_account_file)
            ee.Initialize(credentials, project=project or None)
        elif project:
            ee.Initialize(project=project)
        else:
            ee.Initialize()
    except Exception as exc:
        message = str(exc)
        if "serviceusage.services.use" in message or "required permission to use project" in message:
            return False, f"Projeto Earth Engine identificado ({project}), mas o usuário local não tem permissão serviceusage.services.use."
        return False, message
    return True, f"Earth Engine inicializado com o projeto {project}."


def gee_diagnostics() -> Dict[str, object]:
    settings = earth_engine_settings()
    credential_path = (
        os.getenv("EE_CREDENTIALS_PATH")
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        or settings.get("service_account_file")
    )
    local_user_credentials = Path.home() / ".config" / "earthengine" / "credentials"
    return {
        "earthengine_module_available": ee is not None,
        "project": earth_engine_project(),
        "credentials_path": credential_path,
        "credentials_path_exists": bool(credential_path and Path(credential_path).exists()),
        "local_user_credentials": str(local_user_credentials),
        "local_user_credentials_exists": local_user_credentials.exists(),
        "auth_config_path": str(AUTH_CONFIG_PATH),
        "auth_config_exists": AUTH_CONFIG_PATH.exists(),
        "geo_path": str(GEO_PATH),
        "geo_path_exists": GEO_PATH.exists(),
    }


@st.cache_data(show_spinner=False)
def load_gee_catalog() -> Dict[str, object]:
    ok, message = initialize_earth_engine()
    if not ok:
        return {"ok": False, "message": message, "collections": SATELLITE_OPTIONS}

    loaded = {}
    for name, collection_id in SATELLITE_OPTIONS.items():
        if "/" not in collection_id or " / " in collection_id:
            loaded[name] = collection_id
            continue
        try:
            size = ee.ImageCollection(collection_id).limit(1).size().getInfo()
            loaded[name] = {"collection": collection_id, "sample_available": bool(size)}
        except Exception as exc:
            loaded[name] = {"collection": collection_id, "error": str(exc)}
    return {"ok": True, "message": message, "collections": loaded}


def _roi_geometry(roi_geojson: Dict | None):
    if roi_geojson:
        return ee.Geometry(roi_geojson)
    return None


def _tile_url(ee_image, vis_params: Dict) -> str:
    map_id = ee_image.getMapId(vis_params)
    return map_id["tile_fetcher"].url_format


def build_tile_url(ee_image, vis_params: Dict) -> str:
    return _tile_url(ee_image, vis_params)


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


def _nearest_image(collection, reference_iso: str | None = None, search_days=(1, 3, 7, 15, 30)):
    if ee is None:
        return None
    if reference_iso:
        try:
            parsed = datetime.fromisoformat(reference_iso)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            reference = parsed.astimezone(timezone.utc)
        except Exception:
            reference = datetime.now(timezone.utc)
    else:
        reference = datetime.now(timezone.utc)

    reference_ms = int(reference.timestamp() * 1000)
    for days in search_days:
        start = reference - timedelta(days=days)
        end = reference + timedelta(days=days)
        window = collection.filterDate(start.isoformat(), end.isoformat())
        if window.size().getInfo() == 0:
            continue

        def set_delta(image):
            delta = ee.Number(image.get("system:time_start")).subtract(reference_ms).abs()
            return image.set("time_delta_ms", delta)

        return window.map(set_delta).sort("time_delta_ms").first()
    return None


def _image_datetime(image) -> str:
    try:
        millis = image.get("system:time_start").getInfo()
        if not millis:
            return ""
        return format_datetime_brasilia(datetime.fromtimestamp(millis / 1000, tz=timezone.utc))
    except Exception:
        return ""


def _latest_collection_datetime(collection) -> str:
    try:
        if collection.size().getInfo() == 0:
            return ""
        return _image_datetime(collection.sort("system:time_start", False).first())
    except Exception:
        return ""


def _period_label(start: date, end: date) -> str:
    return format_period_brasilia(
        datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc),
        datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc),
    )


@st.cache_data(show_spinner=False)
def build_gee_tile_layers(
    roi_geojson: Dict | None,
    indicators: List[str],
    reference_iso: str | None = None,
) -> List[Dict[str, str]]:
    ok, _ = initialize_earth_engine()
    if not ok or ee is None or not indicators:
        return []

    roi = _roi_geometry(roi_geojson)
    end = _reference_date(reference_iso)
    start_recent = end - timedelta(days=30)
    start_burned = end - timedelta(days=120)
    layers: List[Dict[str, str]] = []

    for indicator in indicators:
        try:
            if indicator == "VIIRS 375 m":
                collection = (
                    ee.ImageCollection("NASA/VIIRS/002/VNP14A1")
                    .filterDate(start_recent.isoformat(), end.isoformat())
                )
                if roi:
                    collection = collection.filterBounds(roi)
                image = collection.select("MaxFRP").max().selfMask()
                latest_datetime = _latest_collection_datetime(collection)
                layers.append(
                    {
                        "name": "GE | Hotspots VIIRS recentes",
                        "url": _tile_url(image, {"min": 0, "max": 80, "palette": ["#ffff00", "#ff7a00", "#ff0000", "#ffffff"]}),
                        "opacity": 0.92,
                        "indicator": indicator,
                        "source": "VIIRS 375 m",
                        "image_datetime": latest_datetime,
                        "period": _period_label(start_recent, end),
                        "composition": "MaxFRP maximo no periodo",
                    }
                )
            elif indicator == "FIRMS MODIS":
                collection = ee.ImageCollection("FIRMS").filterDate(start_recent.isoformat(), end.isoformat())
                if roi:
                    collection = collection.filterBounds(roi)
                image = collection.select("confidence").max().selfMask()
                latest_datetime = _latest_collection_datetime(collection)
                layers.append(
                    {
                        "name": "GE | Focos de calor MODIS",
                        "url": _tile_url(image, {"min": 30, "max": 100, "palette": ["#ffd400", "#ff6600", "#d40000"]}),
                        "opacity": 0.95,
                        "indicator": indicator,
                        "source": "FIRMS MODIS",
                        "image_datetime": latest_datetime,
                        "period": _period_label(start_recent, end),
                        "composition": "confidence maximo no periodo",
                    }
                )
            elif indicator == "MODIS Burned Area":
                collection = ee.ImageCollection("MODIS/061/MCD64A1").filterDate(start_burned.isoformat(), end.isoformat())
                if roi:
                    collection = collection.filterBounds(roi)
                image = collection.select("BurnDate").max().selfMask()
                latest_datetime = _latest_collection_datetime(collection)
                layers.append(
                    {
                        "name": "GE | Área queimada MODIS",
                        "url": _tile_url(image, {"min": 1, "max": 366, "palette": ["#5b1300", "#d9480f", "#ffd166"]}),
                        "opacity": 0.55,
                        "indicator": indicator,
                        "source": "MODIS Burned Area",
                        "image_datetime": latest_datetime,
                        "period": _period_label(start_burned, end),
                        "composition": "BurnDate maximo no periodo",
                    }
                )
            elif indicator == "MODIS Terra FireMask":
                collection = ee.ImageCollection("MODIS/061/MOD14A1").filterDate(start_recent.isoformat(), end.isoformat())
                if roi:
                    collection = collection.filterBounds(roi)
                image = collection.select("MaxFRP").max().selfMask()
                latest_datetime = _latest_collection_datetime(collection)
                layers.append(
                    {
                        "name": "GE | MODIS Terra anomalia térmica",
                        "url": _tile_url(image, {"min": 0, "max": 80, "palette": ["#fff200", "#ff8c00", "#ff0000", "#ffffff"]}),
                        "opacity": 0.90,
                        "indicator": indicator,
                        "source": "MODIS Terra FireMask",
                        "image_datetime": latest_datetime,
                        "period": _period_label(start_recent, end),
                        "composition": "MaxFRP maximo no periodo",
                    }
                )
            elif indicator == "GOES-16 Hot Spot":
                collection = ee.ImageCollection("NOAA/GOES/16/FDCF")
                if roi:
                    collection = collection.filterBounds(roi)
                nearest = _nearest_image(collection, reference_iso, search_days=(1, 2, 3, 7, 15))
                if nearest is None:
                    continue
                image = nearest.select("Power").selfMask()
                image_datetime = _image_datetime(nearest)
                layers.append(
                    {
                        "name": "GE | GOES-16 Hot Spot",
                        "url": _tile_url(image, {"min": 0, "max": 400, "palette": ["#ffff00", "#ff9900", "#ff0000", "#ff00ff"]}),
                        "opacity": 0.88,
                        "indicator": indicator,
                        "source": "GOES-16 FDCF",
                        "image_datetime": image_datetime,
                        "period": "Imagem mais proxima da referencia",
                        "composition": "Power da imagem GOES mais proxima",
                    }
                )
        except Exception as exc:
            layers.append({"name": f"GE | {indicator} indisponível", "error": str(exc)})
    return layers
