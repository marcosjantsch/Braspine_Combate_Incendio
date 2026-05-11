# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import List, Tuple

from pyproj import Geod, Transformer
from shapely.geometry import LineString, Point

GEOD = Geod(ellps="WGS84")
TO_WEB_MERCATOR = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
TO_WGS84 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)


def fit_center(gdf) -> Tuple[float, float]:
    bounds = gdf.total_bounds
    return (float((bounds[1] + bounds[3]) / 2), float((bounds[0] + bounds[2]) / 2))


def endpoint(lon: float, lat: float, angle: float, range_km: float) -> Tuple[float, float]:
    end_lon, end_lat, _ = GEOD.fwd(lon, lat, angle, range_km * 1000)
    return end_lon, end_lat


def bearing_between(start_lon: float, start_lat: float, end_lon: float, end_lat: float) -> float:
    forward_azimuth, _, _ = GEOD.inv(start_lon, start_lat, end_lon, end_lat)
    return (forward_azimuth + 360) % 360


def segment_intersections(segments: List[LineString]) -> List[Point]:
    points: List[Point] = []
    metric_segments = [
        LineString([TO_WEB_MERCATOR.transform(float(lon), float(lat)) for lon, lat in segment.coords])
        for segment in segments
    ]
    for i, first in enumerate(metric_segments):
        for second in metric_segments[i + 1 :]:
            inter = first.intersection(second)
            if inter.is_empty:
                continue
            if inter.geom_type == "Point":
                lon, lat = TO_WGS84.transform(inter.x, inter.y)
                points.append(Point(lon, lat))
            elif hasattr(inter, "geoms"):
                for geom in inter.geoms:
                    if geom.geom_type == "Point":
                        lon, lat = TO_WGS84.transform(geom.x, geom.y)
                        points.append(Point(lon, lat))
    return points


def parse_decimal_degrees(lat_value: str, lon_value: str) -> Tuple[float, float]:
    lat = float(str(lat_value).replace(",", ".").strip())
    lon = float(str(lon_value).replace(",", ".").strip())
    return lat, lon


def parse_dms(value: str) -> float:
    text = str(value).strip().upper().replace(",", ".")
    sign = -1 if any(marker in text for marker in ["S", "W", "O"]) or text.startswith("-") else 1
    numbers = [float(part) for part in re.findall(r"\d+(?:\.\d+)?", text)]
    if not numbers:
        raise ValueError("Coordenada GMS inválida.")
    degrees = numbers[0]
    minutes = numbers[1] if len(numbers) > 1 else 0.0
    seconds = numbers[2] if len(numbers) > 2 else 0.0
    return sign * (degrees + minutes / 60.0 + seconds / 3600.0)


def parse_dms_pair(lat_value: str, lon_value: str) -> Tuple[float, float]:
    return parse_dms(lat_value), parse_dms(lon_value)


def utm_to_decimal_degrees(easting: float, northing: float, zone: int, hemisphere: str) -> Tuple[float, float]:
    hemi = str(hemisphere).strip().upper()
    epsg = 32700 + int(zone) if hemi == "S" else 32600 + int(zone)
    transformer = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    lon, lat = transformer.transform(float(easting), float(northing))
    return lat, lon
