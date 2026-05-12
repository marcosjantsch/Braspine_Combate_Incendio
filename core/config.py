# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]


ENVIRONMENT_PROFILES = {
    "streamelit": {
        "aliases": {"streamelit", "streamlit", "coldroom", "cloudrun", "avant", "gfp"},
        "title": "Avant Plataforma de Auxílio de Combate a Incêndios Florestais",
        "ee_project": "streamelit",
        "asset_fazendas_gee": "projects/streamelit/assets/GFP/Base_GFP_Brasil_Dezembro_2025_geolimits",
    },
    "braspine": {
        "aliases": {"braspine", "braspineincendio"},
        "title": "Braspine Plataforma de Auxílio de Combate a Incêndios Florestais",
        "ee_project": "braspine",
        "asset_fazendas_gee": "projects/braspine/assets/GFP/Base_GFP_Brasil_Dezembro_2025_geolimits",
    },
}


def _normalize_environment(value: str | None) -> str:
    candidate = str(value or "").strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    for environment, profile in ENVIRONMENT_PROFILES.items():
        aliases = {environment, *profile["aliases"]}
        if candidate in aliases or any(alias and alias in candidate for alias in aliases):
            return environment
    return ""


def _detect_environment() -> str:
    explicit = (
        os.getenv("APP_ENV")
        or os.getenv("GFP_ENV")
        or os.getenv("APP_PROFILE")
        or os.getenv("GFP_PROFILE")
        or os.getenv("CLIENT_ENV")
    )
    environment = _normalize_environment(explicit)
    if environment:
        return environment

    project_environment = _normalize_environment(os.getenv("EE_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT"))
    if project_environment:
        return project_environment

    folder_hint = _normalize_environment(BASE_DIR.name)
    if folder_hint:
        return folder_hint

    parent_hint = _normalize_environment(BASE_DIR.parent.name)
    if parent_hint:
        return parent_hint

    return "streamelit"


APP_ENVIRONMENT = _detect_environment()
APP_PROFILE = ENVIRONMENT_PROFILES[APP_ENVIRONMENT]
APP_TITLE = APP_PROFILE["title"]
APP_VERSION = "1.0"
APP_VERSION_UPDATED_AT = "12/05/2026 11:44:12"
AUTH_CONFIG_PATH = Path(
    os.getenv("APP_AUTH_CONFIG")
    or os.getenv(f"{APP_ENVIRONMENT.upper()}_AUTH_CONFIG")
    or os.getenv("CODEBOOK_AUTH_CONFIG")
    or os.getenv("CLOUDRON_AUTH_CONFIG")
    or (BASE_DIR / "auth" / f"config.{APP_ENVIRONMENT}.yaml" if (BASE_DIR / "auth" / f"config.{APP_ENVIRONMENT}.yaml").exists() else "")
    or BASE_DIR / "auth" / "config.yaml"
)
GEO_PATH = Path(
    os.getenv("APP_GEO_PATH")
    or os.getenv(f"{APP_ENVIRONMENT.upper()}_GEO_PATH")
    or os.getenv("CODEBOOK_GEO_PATH")
    or os.getenv("CLOUDRON_GEO_PATH")
    or BASE_DIR / "data" / "Geo.shp"
)

DEFAULT_RANGE_KM = 25.0
DEFAULT_EE_PROJECT = APP_PROFILE["ee_project"]
ASSET_FAZENDAS_GEE = APP_PROFILE["asset_fazendas_gee"]
SIMPLIFICATION_TOLERANCE = 0.001

SATELLITE_OPTIONS = {
    "FIRMS MODIS": "FIRMS",
    "VIIRS 375 m": "NASA/VIIRS/002/VNP14A1",
    "NASA GIBS Hotspots": "NASA_GIBS_THERMAL_ANOMALIES",
    "MODIS Terra FireMask": "MODIS/061/MOD14A1",
    "MODIS Burned Area": "MODIS/061/MCD64A1",
    "Risco de incendio florestal": "DERIVED_FIRE_RISK",
    "GOES visual meteorologico": "DERIVED_GOES_VISUAL",
    "GOES temperatura de brilho": "DERIVED_GOES_THERMAL",
    "GOES hotspots recentes": "DERIVED_GOES_HOTSPOT",
    "INPE Queimadas": "INPE_BDQUEIMADAS_CSV",
    "NOAA HMS Smoke": "NOAA_HMS_SMOKE_SHAPEFILE",
    "CAMS aerossois/fumaca": "DERIVED_CAMS_SENTINEL5P_SMOKE",
    "Sentinel-3 SLSTR": "UNCONFIGURED_SENTINEL3_SLSTR",
    "Landsat Collection 2 Thermal": "LANDSAT/LC08/C02/T1_L2",
    "Sentinel-2 NDVI/NBR": "COPERNICUS/S2_SR_HARMONIZED",
    "ERA5 Land": "ECMWF/ERA5_LAND/DAILY_AGGR",
    "ECMWF Fire Weather Index": "UNCONFIGURED_ECMWF_FWI",
    "GOES GLM Lightning": "UNCONFIGURED_GOES_GLM",
    "SMAP umidade do solo": "UNCONFIGURED_SMAP",
}

SATELLITE_DESCRIPTIONS = {
    "FIRMS MODIS": "Focos ativos de calor e fogo nas ultimas 48 horas da referencia.",
    "VIIRS 375 m": "Hotspots recentes em maior resolucao nas ultimas 48 horas da referencia.",
    "NASA GIBS Hotspots": "Camadas NASA GIBS/FIRMS de anomalias termicas VIIRS/MODIS, com pontos usados no calculo de distancia.",
    "MODIS Terra FireMask": "Anomalias termicas MODIS Terra nas ultimas 48 horas da referencia.",
    "MODIS Burned Area": "Area queimada consolidada mensal.",
    "Risco de incendio florestal": "Indice derivado de clima, vegetacao, agua e focos ativos.",
    "GOES visual meteorologico": "Imagem GOES mais recente para acompanhamento visual regional.",
    "GOES temperatura de brilho": "Canal termal GOES para nuvens, fumaca e anomalias.",
    "GOES hotspots recentes": "Hotspots GOES acumulados nas ultimas 48 horas da referencia, quando disponiveis.",
    "INPE Queimadas": "Focos oficiais do Programa Queimadas/BDQueimadas, filtrados pela ROI e usados no calculo de distancia.",
    "NOAA HMS Smoke": "Poligonos diarios oficiais de fumaca NOAA HMS, recortados pela ROI.",
    "CAMS aerossois/fumaca": "Contexto atmosferico com Sentinel-5P Aerosol Index e CAMS PM2.5.",
    "Sentinel-3 SLSTR": "Camada termal complementar quando configurada.",
    "Landsat Collection 2 Thermal": "Analise termal contextual nas ultimas 24 horas da referencia.",
    "Sentinel-2 NDVI/NBR": "Vegetacao, combustivel e cicatriz nas ultimas 24 horas da referencia.",
    "ERA5 Land": "Temperatura/variaveis climaticas nas ultimas 24 horas da referencia.",
    "ECMWF Fire Weather Index": "Indice meteorologico FWI quando configurado.",
    "GOES GLM Lightning": "Descargas eletricas quando configurado.",
    "SMAP umidade do solo": "Umidade do solo quando configurada.",
}
