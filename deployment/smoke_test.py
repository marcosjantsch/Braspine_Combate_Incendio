# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


REQUIRED_MODULES = [
    "streamlit",
    "streamlit_folium",
    "geopandas",
    "pandas",
    "plotly",
    "folium",
    "branca",
    "shapely",
    "pyproj",
    "fiona",
    "yaml",
    "bcrypt",
    "ee",
    "requests",
    "openpyxl",
]


def check_imports() -> list[str]:
    errors: list[str] = []
    for module_name in REQUIRED_MODULES:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            errors.append(f"{module_name}: {exc}")
    return errors


def main() -> int:
    import geopandas as gpd

    from core.config import AUTH_CONFIG_PATH, GEO_PATH
    from core.data_service import configure_proj_data
    from services.gee_service import gee_diagnostics

    print("== Import checks ==")
    import_errors = check_imports()
    if import_errors:
        for error in import_errors:
            print(f"ERROR {error}")
        return 1
    print("OK imports")

    print("\n== Path checks ==")
    print(f"auth_config={AUTH_CONFIG_PATH} exists={AUTH_CONFIG_PATH.exists()}")
    print(f"geo_path={GEO_PATH} exists={GEO_PATH.exists()}")
    if not AUTH_CONFIG_PATH.exists():
        print("WARN auth config missing. Mount auth/config.yaml or set APP_AUTH_CONFIG.")
    if not GEO_PATH.exists():
        print("ERROR shapefile missing. Mount data/ or set APP_GEO_PATH.")
        return 1

    print("\n== Geospatial checks ==")
    configure_proj_data()
    try:
        sample = gpd.read_file(GEO_PATH, rows=1, engine="fiona")
        print(f"OK shapefile rows_sample={len(sample)} crs={sample.crs}")
    except Exception as exc:
        print(f"ERROR shapefile read failed: {exc}")
        return 1

    print("\n== Earth Engine diagnostics ==")
    diag = gee_diagnostics()
    for key, value in diag.items():
        print(f"{key}={value}")

    print("\nSmoke test completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
