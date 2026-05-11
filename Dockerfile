FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    PORT=8080 \
    FOLIUM_RENDERER=html \
    PROJ_LIB=/usr/share/proj \
    GDAL_DATA=/usr/share/gdal \
    APP_AUTH_CONFIG=/app/auth/config.yaml \
    APP_GEO_PATH=/app/data/Geo.shp

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        gdal-bin \
        libgdal-dev \
        libgeos-dev \
        libproj-dev \
        proj-bin \
        proj-data \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /app/requirements.txt

COPY . /app

RUN mkdir -p /app/data/cache/noaa_hms_smoke /app/data/cache/inpe_queimadas

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; port=os.environ.get('PORT', '8080'); urllib.request.urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=3).read()" || exit 1

CMD ["python", "deployment/start_streamlit.py"]
