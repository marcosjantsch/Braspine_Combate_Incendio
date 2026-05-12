# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
from datetime import datetime, timezone
from html import escape
from typing import Dict

import streamlit as st
from core.config import APP_ENVIRONMENT, APP_TITLE, APP_VERSION, APP_VERSION_UPDATED_AT, BASE_DIR


ENVIRONMENT_LABELS = {
    "streamelit": "GFP",
    "braspine": "Braspine",
}


def _logo_data_uri() -> str:
    logo_path = BASE_DIR / "assets" / "logo-header.png"
    if not logo_path.exists():
        return ""
    encoded = base64.b64encode(logo_path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def render_top_header(user: Dict) -> None:
    session_started_at = st.session_state.setdefault(
        "session_started_at",
        datetime.now(timezone.utc).isoformat(),
    )
    username = escape(str(user.get("username", "")))
    name = escape(str(user.get("name", "")))
    role = escape(str(user.get("role", "")))
    environment_label = escape(ENVIRONMENT_LABELS.get(APP_ENVIRONMENT, APP_ENVIRONMENT or "GFP"))
    logo_src = _logo_data_uri()
    logo_html = f'<img class="fire-logo" src="{logo_src}" alt="Avante">' if logo_src else ""
    st.markdown(
        f"""
        <div class="fire-header">
            <div class="fire-brand">
                {logo_html}
                <div>
                    <div class="fire-title">{APP_TITLE}</div>
                    <div class="fire-subtitle">Selecao de projeto, indicadores GE e mapa operacional.</div>
                </div>
            </div>
            <div class="fire-session">
                <strong>Sessao atual</strong><br>
                Usuario: {name} | Perfil: {role} | Login: {username}<br>
                Ambiente: {environment_label} | Versao: {APP_VERSION} | Atualizacao: {APP_VERSION_UPDATED_AT}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
