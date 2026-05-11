# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    port = os.getenv("PORT", "8080").strip() or "8080"
    print(f"[startup] Starting Streamlit on 0.0.0.0:{port}", flush=True)
    print(f"[startup] APP_AUTH_CONFIG={os.getenv('APP_AUTH_CONFIG', '')}", flush=True)
    print(f"[startup] APP_GEO_PATH={os.getenv('APP_GEO_PATH', '')}", flush=True)
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "app.py",
        "--server.address=0.0.0.0",
        f"--server.port={port}",
        "--server.headless=true",
        "--server.enableCORS=false",
        "--server.enableXsrfProtection=false",
        "--browser.gatherUsageStats=false",
    ]
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
