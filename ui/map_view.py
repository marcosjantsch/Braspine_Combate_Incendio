# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from typing import Dict, Iterable, List

import folium
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from branca.element import MacroElement, Template
from pyproj import Transformer
from shapely.geometry import LineString, Point
from streamlit_folium import st_folium

from core.geometry_service import bearing_between, endpoint, fit_center, segment_intersections

WEB_MERCATOR = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
LINE_SELECTION_THRESHOLD_M = 1200


class ModifierClickFilter(MacroElement):
    def __init__(self):
        super().__init__()
        self._template = Template(
            """
            {% macro script(this, kwargs) %}
            (function() {
                const map = {{ this._parent.get_name() }};
                if (map.__modifierClickFilterInstalled) {
                    return;
                }
                map.__modifierClickFilterInstalled = true;
                const originalOn = map.on.bind(map);
                map.on = function(types, fn, context) {
                    if (types === "click" && typeof fn === "function") {
                        const wrapped = function(event) {
                            const original = event && event.originalEvent;
                            if (!original || (!original.ctrlKey && !original.shiftKey)) {
                                return;
                            }
                            event.latlng.modifier = original.shiftKey ? "shift" : "ctrl";
                            return fn.call(this, event);
                        };
                        return originalOn(types, wrapped, context);
                    }
                    return originalOn(types, fn, context);
                };
            })();
            {% endmacro %}
            """
        )


def farm_popup(row) -> str:
    empresa = row.get("EMPRESA", "")
    fazenda = row.get("FAZENDA", "")
    return f"<b>{empresa}</b><br>{fazenda}"


def add_farms_to_map(map_obj: folium.Map, gdf, selected_companies: Iterable[str]) -> None:
    selected = set(selected_companies)
    if not selected:
        return

    farm_view = gdf[gdf["EMPRESA"].astype(str).str.strip().isin(selected)].copy()
    if farm_view.empty:
        return

    if st.session_state.get("show_original_polygons") and "__geometry_original__" in farm_view.columns:
        farm_view["geometry"] = farm_view["__geometry_original__"]

    render_gdf = farm_view.drop(columns=["__geometry_original__"], errors="ignore").copy()
    popup_fields = [field for field in ["EMPRESA", "FAZENDA"] if field in render_gdf.columns]
    keep_columns = popup_fields + ["geometry"]
    render_gdf = render_gdf[keep_columns].copy()
    for column in popup_fields:
        if pd.api.types.is_datetime64_any_dtype(render_gdf[column]):
            render_gdf[column] = render_gdf[column].dt.strftime("%Y-%m-%d")
        else:
            render_gdf[column] = render_gdf[column].astype(str)

    folium.GeoJson(
        data=render_gdf.to_json(),
        name="Empresas selecionadas",
        style_function=lambda _: {
            "color": "#ffcf4a",
            "weight": 2.4,
            "fillColor": "#2d7a55",
            "fillOpacity": 0.18,
            "opacity": 0.95,
        },
        highlight_function=lambda _: {
            "color": "#ffffff",
            "weight": 3.4,
            "fillColor": "#ffcf4a",
            "fillOpacity": 0.28,
            "opacity": 1,
        },
        popup=folium.GeoJsonPopup(fields=popup_fields, labels=True) if popup_fields else None,
        tooltip=folium.GeoJsonTooltip(fields=popup_fields, labels=True) if popup_fields else None,
    ).add_to(map_obj)


def add_roi_to_map(map_obj: folium.Map) -> None:
    bounds = st.session_state.get("applied_roi_bounds")
    if not bounds or not isinstance(bounds, list) or len(bounds) != 2:
        return
    try:
        south, west = float(bounds[0][0]), float(bounds[0][1])
        north, east = float(bounds[1][0]), float(bounds[1][1])
    except Exception:
        return
    roi_group = folium.FeatureGroup(name="ROI aplicada", show=False)
    folium.Rectangle(
        bounds=[[south, west], [north, east]],
        color="#38bdf8",
        weight=2,
        fill=True,
        fill_color="#38bdf8",
        fill_opacity=0.06,
        dash_array="8,6",
        tooltip="ROI usada na ultima consulta GE",
    ).add_to(roi_group)
    roi_group.add_to(map_obj)


def _smoke_density_style(feature: Dict) -> Dict:
    density = str(feature.get("properties", {}).get("Density", "")).strip().lower()
    if "heavy" in density or "thick" in density:
        color = "#7f1d1d"
        fill_opacity = 0.42
    elif "medium" in density or "moderate" in density:
        color = "#f97316"
        fill_opacity = 0.34
    else:
        color = "#facc15"
        fill_opacity = 0.24
    return {
        "color": color,
        "weight": 1.4,
        "fillColor": color,
        "fillOpacity": fill_opacity,
        "opacity": 0.85,
    }


def add_geojson_overlay(map_obj: folium.Map, layer: Dict) -> None:
    geojson = layer.get("geojson")
    if not geojson:
        return
    if layer.get("layer_type") == "point_geojson":
        try:
            data = json.loads(geojson) if isinstance(geojson, str) else geojson
        except Exception:
            data = {}
        fields = layer.get("fields") or []
        group = folium.FeatureGroup(name=layer.get("name", "Pontos vetoriais"), show=bool(layer.get("show", True)))
        for feature in data.get("features", []):
            geometry = feature.get("geometry", {})
            if geometry.get("type") != "Point":
                continue
            coords = geometry.get("coordinates") or []
            if len(coords) < 2:
                continue
            props = feature.get("properties", {})
            popup_lines = [f"<b>{layer.get('name', 'Ponto')}</b>"]
            for field in fields:
                popup_lines.append(f"{field}: {props.get(field, '')}")
            folium.CircleMarker(
                [float(coords[1]), float(coords[0])],
                radius=5,
                color="#052e16",
                weight=1,
                fill=True,
                fill_color=str(layer.get("color", "#22c55e")),
                fill_opacity=0.9,
                tooltip=str(props.get("satelite") or layer.get("name", "Ponto")),
                popup="<br>".join(popup_lines),
            ).add_to(group)
        group.add_to(map_obj)
        return
    fields = layer.get("fields") or ["Density", "Satellite", "Start", "End_"]
    folium.GeoJson(
        data=geojson,
        name=layer.get("name", "Camada vetorial"),
        style_function=_smoke_density_style if layer.get("layer_type") == "smoke_geojson" else None,
        tooltip=folium.GeoJsonTooltip(fields=fields, labels=True) if fields else None,
        popup=folium.GeoJsonPopup(fields=fields, labels=True) if fields else None,
        show=bool(layer.get("show", True)),
    ).add_to(map_obj)


def add_detection_points_to_map(map_obj: folium.Map) -> None:
    summary = st.session_state.get("fire_detection_summary", {})
    points = summary.get("points", []) if isinstance(summary, dict) else []
    if not points:
        return
    colors = {
        "FIRMS MODIS": "#ff7a00",
        "MODIS FIRMS": "#ff7a00",
        "VIIRS": "#ff003c",
        "MODIS Terra FireMask": "#ffd400",
        "MODIS anomalia": "#ffd400",
        "GOES": "#c026d3",
        "GOES Hotspot/FDCF": "#c026d3",
        "NASA GIBS Hotspots": "#ff00ff",
        "NASA GIBS VIIRS": "#ff003c",
        "NASA GIBS MODIS": "#ff7a00",
        "INPE Queimadas": "#22c55e",
        "BDQueimadas": "#22c55e",
        "NOAA HMS Smoke": "#64748b",
    }
    group = folium.FeatureGroup(name="Focos detectados", show=True)
    grouped_points: Dict[str, List[Dict]] = {}
    for point in points:
        grouped_points.setdefault(str(point.get("source_key") or point.get("source") or "fonte"), []).append(point)
    visible_points: List[Dict] = []
    per_source_limit = 400
    total_limit = 1600
    for source_points in grouped_points.values():
        visible_points.extend(source_points[:per_source_limit])
    visible_points = visible_points[:total_limit]
    for point in visible_points:
        try:
            lat = float(point["lat"])
            lon = float(point["lon"])
        except Exception:
            continue
        source = str(point.get("source", "Foco"))
        event_type = str(point.get("event_type", source))
        satellite = str(point.get("satellite", source))
        folium.CircleMarker(
            [lat, lon],
            radius=5 if "anomalia" in source.lower() else 4,
            color="#111827",
            weight=1,
            fill=True,
            fill_color=colors.get(source, colors.get(satellite, "#ef4444")),
            fill_opacity=0.85,
            tooltip=f"{event_type} | {satellite}",
        ).add_to(group)
    group.add_to(map_obj)


def add_hotspot_focus_to_map(map_obj: folium.Map) -> None:
    focus = st.session_state.get("hotspot_focus")
    if not focus:
        return
    try:
        lat = float(focus["lat"])
        lon = float(focus["lon"])
    except Exception:
        return
    popup = (
        f"<b>{focus.get('tipo', 'Foco')}</b><br>"
        f"Satélite: {focus.get('satelite', '')}<br>"
        f"Empresa: {focus.get('empresa', '')}<br>"
        f"Fazenda: {focus.get('fazenda', '')}<br>"
        f"Município/UF: {focus.get('municipio', '')}/{focus.get('uf', '')}<br>"
        f"Distância: {focus.get('distancia_km', '')} km"
    )
    focus_group = folium.FeatureGroup(name="Foco selecionado", show=True)
    folium.CircleMarker(
        [lat, lon],
        radius=11,
        color="#ffffff",
        weight=3,
        fill=True,
        fill_color="#38bdf8",
        fill_opacity=0.95,
        popup=popup,
        tooltip="Foco selecionado na tabela",
    ).add_to(focus_group)
    folium.Circle(
        [lat, lon],
        radius=250,
        color="#38bdf8",
        weight=2,
        fill=False,
        opacity=0.95,
    ).add_to(focus_group)
    focus_group.add_to(map_obj)


def _tower_segment(point: dict, range_km: float) -> tuple[float, float, LineString]:
    end_lon, end_lat = endpoint(point["lon"], point["lat"], point["angle"], range_km)
    return end_lon, end_lat, LineString([(point["lon"], point["lat"]), (end_lon, end_lat)])


def _to_mercator_point(lon: float, lat: float) -> Point:
    x, y = WEB_MERCATOR.transform(float(lon), float(lat))
    return Point(x, y)


def _to_mercator_line(line: LineString) -> LineString:
    return LineString([WEB_MERCATOR.transform(float(lon), float(lat)) for lon, lat in line.coords])


def _nearest_tower_index(lat: float, lon: float, range_km: float) -> int | None:
    click_point = _to_mercator_point(lon, lat)
    nearest_index = None
    nearest_distance = float("inf")
    for idx, point in enumerate(st.session_state.get("triangulation_points", [])):
        _, _, segment = _tower_segment(point, range_km)
        distance = click_point.distance(_to_mercator_line(segment))
        if distance < nearest_distance:
            nearest_distance = distance
            nearest_index = idx
    return nearest_index if nearest_distance <= LINE_SELECTION_THRESHOLD_M else None


def _update_triangulation_row_angle(index: int, angle: float) -> None:
    rows = st.session_state.get("triangulation_rows", [])
    valid_seen = -1
    for row in rows:
        if str(row.get("lon", "")).strip() and str(row.get("lat", "")).strip():
            valid_seen += 1
        if valid_seen == index:
            row["angle"] = float(angle) % 360
            return


def process_map_click(map_output: Dict) -> None:
    if st.session_state.get("active_main_tab") != "Triangulacao":
        return
    click = map_output.get("last_clicked") if map_output else None
    if not click:
        return

    click_key = f"{click['lat']:.8f},{click['lng']:.8f}"
    modifier = str(click.get("modifier", "")).lower()
    if modifier not in {"ctrl", "shift"}:
        return
    click_key = f"{modifier}:{click['lat']:.8f},{click['lng']:.8f}"
    if st.session_state.get("last_map_click") == click_key:
        return
    st.session_state["last_map_click"] = click_key

    if modifier == "shift" and st.session_state.get("select_line_mode") and st.session_state.get("triangulation_points"):
        nearest = _nearest_tower_index(click["lat"], click["lng"], float(st.session_state.get("range_km", 25.0)))
        st.session_state["select_line_mode"] = False
        if nearest is not None:
            st.session_state["rotate_point_index"] = nearest
            st.session_state["rotate_point_mode"] = True
        st.rerun()

    if modifier == "shift" and st.session_state.get("rotate_point_mode") and st.session_state.get("triangulation_points"):
        idx = int(st.session_state.get("rotate_point_index", 0))
        idx = max(0, min(idx, len(st.session_state["triangulation_points"]) - 1))
        point = st.session_state["triangulation_points"][idx]
        angle = bearing_between(point["lon"], point["lat"], click["lng"], click["lat"])
        point["angle"] = angle
        _update_triangulation_row_angle(idx, angle)
        st.session_state["rotate_point_mode"] = False
        st.rerun()

    if modifier == "shift" and st.session_state.get("triangulation_points"):
        nearest = _nearest_tower_index(click["lat"], click["lng"], float(st.session_state.get("range_km", 25.0)))
        if nearest is not None:
            st.session_state["rotate_point_index"] = nearest
            st.session_state["rotate_point_mode"] = True
            st.rerun()

    if modifier == "ctrl":
        st.session_state["pending_tower_click"] = {"lon": click["lng"], "lat": click["lat"]}
        st.rerun()


def build_main_map(
    gdf,
    selected_companies: List[str],
    range_km: float,
    capture_clicks: bool = False,
    capture_bounds: bool = False,
) -> Dict:
    selected = set(selected_companies)
    farm_view = gdf[gdf["EMPRESA"].astype(str).str.strip().isin(selected)] if selected else gdf
    center = fit_center(farm_view if not farm_view.empty else gdf)
    fmap = folium.Map(
        location=center,
        zoom_start=11 if selected_companies else 6,
        tiles=None,
        box_zoom=False,
    )
    if capture_clicks:
        ModifierClickFilter().add_to(fmap)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery",
        name="Satélite",
    ).add_to(fmap)
    folium.TileLayer(
        tiles="OpenStreetMap",
        name="Mapa com estradas",
        control=True,
        show=False,
    ).add_to(fmap)
    add_farms_to_map(fmap, gdf, selected_companies)
    add_roi_to_map(fmap)
    add_detection_points_to_map(fmap)
    add_hotspot_focus_to_map(fmap)
    if st.session_state.get("fit_viewport_on_next_map") and st.session_state.get("viewport_fit_bounds"):
        fmap.fit_bounds(st.session_state["viewport_fit_bounds"])
        st.session_state["fit_viewport_on_next_map"] = False

    for layer in st.session_state.get("gee_tile_layers", []):
        if layer.get("layer_type") == "wms":
            folium.raster_layers.WmsTileLayer(
                url=layer["url"],
                layers=layer.get("wms_layers", ""),
                name=layer["name"],
                attr="NASA GIBS",
                fmt=layer.get("format", "image/png"),
                transparent=bool(layer.get("transparent", True)),
                version=layer.get("version", "1.3.0"),
                overlay=True,
                control=True,
                opacity=float(layer.get("opacity", 0.75)),
                show=bool(layer.get("show", True)),
                time=layer.get("time"),
            ).add_to(fmap)
        elif layer.get("url"):
            folium.TileLayer(
                tiles=layer["url"],
                attr="Google Earth Engine",
                name=layer["name"],
                overlay=True,
                control=True,
                opacity=float(layer.get("opacity", 0.75)),
                show=bool(layer.get("show", True)),
            ).add_to(fmap)
        elif layer.get("geojson"):
            add_geojson_overlay(fmap, layer)

    for layer in st.session_state.get("fire_risk_layers", []):
        if layer.get("url"):
            folium.TileLayer(
                tiles=layer["url"],
                attr="Google Earth Engine",
                name=layer["name"],
                overlay=True,
                control=True,
                opacity=float(layer.get("opacity", 0.65)),
                show=True,
            ).add_to(fmap)

    segments: List[LineString] = []
    intersections = []
    triangulation_points = st.session_state.get("triangulation_points", []) if capture_clicks else []
    if capture_clicks:
        triangulation_group = folium.FeatureGroup(name="Triangulacao", show=True)
        pending = st.session_state.get("pending_tower_click")
        if capture_clicks and pending:
            folium.CircleMarker(
                [pending["lat"], pending["lon"]],
                radius=7,
                color="#ffffff",
                fill=True,
                fill_color="#38bdf8",
                popup=(
                    "<b>Usar esta coordenada?</b><br>"
                    f"Longitude: {pending['lon']:.6f}<br>"
                    f"Latitude: {pending['lat']:.6f}<br>"
                    "Confirme ou recuse na aba Triangulacao."
                ),
            ).add_to(triangulation_group)
        for idx, point in enumerate(triangulation_points, start=1):
            is_selected = (idx - 1) == st.session_state.get("rotate_point_index") and st.session_state.get("rotate_point_mode")
            end_lon, end_lat, segment = _tower_segment(point, range_km)
            folium.CircleMarker(
                [point["lat"], point["lon"]],
                radius=8 if is_selected else 5,
                color="#fff",
                fill=True,
                fill_color="#ffcf4a" if is_selected else "#126f7d",
                popup=f"Torre {idx}",
            ).add_to(triangulation_group)
            folium.PolyLine(
                [[point["lat"], point["lon"]], [end_lat, end_lon]],
                color="#ffcf4a" if is_selected else "#d08a1d",
                weight=6 if is_selected else 3,
                tooltip=f"Ponto {idx}: {point['angle']:.2f} graus / {range_km:.1f} km",
            ).add_to(triangulation_group)
            segments.append(segment)

        intersections = segment_intersections(segments)
        for idx, point in enumerate(intersections, start=1):
            folium.CircleMarker(
                [point.y, point.x],
                radius=6,
                color="#fff",
                fill=True,
                fill_color="#b74236",
                popup=f"Cruzamento {idx}",
            ).add_to(triangulation_group)
        triangulation_group.add_to(fmap)
    has_risk_layer = any(
        str(layer.get("name", "")).startswith("Risco de incendio")
        for layer in st.session_state.get("fire_risk_layers", [])
    )
    if has_risk_layer and st.session_state.get("show_map_legend", True):
        goes_time = st.session_state.get("last_goes_time") or "sem imagem GOES"
        legend_html = f"""
        <div style="
            position: fixed; bottom: 22px; left: 22px; z-index: 9999;
            background: rgba(2, 6, 23, 0.88); color: #ecfdf5;
            padding: 8px 10px; border: 1px solid rgba(52,211,153,.35);
            border-radius: 8px; font-size: 10px; line-height: 1.25;">
            <strong>Risco de incêndio</strong><br>
            <span style="color:#1a9850">■</span> Baixo 0-25<br>
            <span style="color:#ffff00">■</span> Moderado 25-50<br>
            <span style="color:#fdae61">■</span> Alto 50-75<br>
            <span style="color:#d7191c">■</span> Muito alto 75-100<br>
            <span>GOES: {goes_time}</span>
        </div>
        """
        fmap.get_root().html.add_child(folium.Element(legend_html))
    if capture_clicks:
        hint_html = """
        <div style="
            position: fixed; top: 14px; left: 50%; transform: translateX(-50%);
            z-index: 9999; background: rgba(2, 6, 23, 0.86); color: #f8fafc;
            padding: 7px 12px; border: 1px solid rgba(255,207,74,.45);
            border-radius: 999px; font-size: 11px; font-weight: 700;">
            Pressione Ctrl para capturar uma coordenada. Pressione Shift para rotacionar a linha desejada
        </div>
        """
        fmap.get_root().html.add_child(folium.Element(hint_html))
    fmap.get_root().html.add_child(
        folium.Element(
            """
            <style>
            .leaflet-control-layers { font-size: 10px; line-height: 1.2; }
            .leaflet-control-layers label { margin-bottom: 2px; }
            </style>
            """
        )
    )
    folium.LayerControl(collapsed=False, position="topright").add_to(fmap)

    if capture_clicks:
        st.caption(
            "Na aba Triangulacao, ative a captura para criar pontos pelo clique no mapa. "
            "Para rotacionar, selecione um ponto, ative a rotacao e clique na direcao desejada."
        )
    map_key_suffix = "triangulation" if capture_clicks else "operational"
    if capture_bounds:
        map_key_suffix = f"{map_key_suffix}_capture"
    returned_objects = []
    if capture_clicks:
        returned_objects.append("last_clicked")
    if capture_bounds:
        returned_objects.append("bounds")

    renderer = os.getenv("FOLIUM_RENDERER", "html").strip().lower()
    if renderer == "html" and not capture_clicks and not capture_bounds:
        components.html(fmap.get_root().render(), height=650, scrolling=False)
        map_output = {}
    else:
        map_output = st_folium(
            fmap,
            height=620,
            use_container_width=True,
            key=f"main_fire_map_{map_key_suffix}",
            returned_objects=returned_objects,
        )
    st.session_state["intersection_count"] = len(intersections)
    return map_output

