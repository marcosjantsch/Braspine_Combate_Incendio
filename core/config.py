# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path

APP_TITLE = "Plataforma de Auxílio ao Combate a Incêndios Florestais"
BASE_DIR = Path(__file__).resolve().parents[1]
AUTH_CONFIG_PATH = Path(
    os.getenv("APP_AUTH_CONFIG")
    or os.getenv("CODEBOOK_AUTH_CONFIG")
    or BASE_DIR / "auth" / "config.yaml"
)
GEO_PATH = Path(
    os.getenv("APP_GEO_PATH")
    or os.getenv("CODEBOOK_GEO_PATH")
    or os.getenv("CLOUDRON_GEO_PATH")
    or BASE_DIR / "data" / "Geo.shp"
)

DEFAULT_RANGE_KM = 25.0
DEFAULT_EE_PROJECT = "streamelit"
ASSET_FAZENDAS_GEE = "projects/streamelit/assets/GFP/Base_GFP_Brasil_Dezembro_2025_geolimits"
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
