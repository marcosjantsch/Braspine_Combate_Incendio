$ErrorActionPreference = "Stop"

if (-not (Test-Path ".\requirements.txt")) {
    Write-Error "Execute este script na raiz do projeto."
}

if (-not (Test-Path ".\.venv")) {
    python -m venv .venv
}

.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python deployment\smoke_test.py
python -m streamlit run app.py --server.port 8502 --server.address 127.0.0.1
