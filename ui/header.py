# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Dict

import streamlit as st
import streamlit.components.v1 as components

from core.config import APP_TITLE


def render_top_header(user: Dict) -> None:
    session_started_at = st.session_state.setdefault(
        "session_started_at",
        datetime.now(timezone.utc).isoformat(),
    )
    username = escape(str(user.get("username", "")))
    name = escape(str(user.get("name", "")))
    role = escape(str(user.get("role", "")))
    st.markdown(
        f"""
        <div class="fire-header">
            <div>
                <div class="fire-title">{APP_TITLE}</div>
                <div class="fire-subtitle">Selecao de projeto, indicadores GE e mapa operacional.</div>
            </div>
            <div class="fire-session">
                <strong>Sessao atual</strong><br>
                Usuario: {name} | Perfil: {role} | Login: {username}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    components.html(
        f"""
        <div class="fire-session-runtime">
            <div>
                <strong>Sessao atual</strong>
                <span>Usuario: {name} | Login: {username}</span>
            </div>
            <div class="fire-session-clock">
                Tempo aberto: <strong id="session-elapsed">00:00:00</strong>
            </div>
        </div>
        <script>
        const startedAt = new Date("{session_started_at}").getTime();
        const elapsedEl = document.getElementById("session-elapsed");
        function pad(value) {{
            return String(value).padStart(2, "0");
        }}
        function tickSession() {{
            const total = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
            const hours = Math.floor(total / 3600);
            const minutes = Math.floor((total % 3600) / 60);
            const seconds = total % 60;
            elapsedEl.textContent = `${{pad(hours)}}:${{pad(minutes)}}:${{pad(seconds)}}`;
        }}
        tickSession();
        window.clearInterval(window.__fireSessionClock);
        window.__fireSessionClock = window.setInterval(tickSession, 1000);
        </script>
        <style>
        body {{
            margin: 0;
            background: transparent;
            color: #d1fae5;
            font-family: "Source Sans Pro", sans-serif;
        }}
        .fire-session-runtime {{
            box-sizing: border-box;
            width: 100%;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            padding: 10px 14px;
            border: 1px solid rgba(52, 211, 153, 0.18);
            border-radius: 12px;
            background: rgba(2, 6, 23, 0.68);
        }}
        .fire-session-runtime strong {{
            color: #ecfdf5;
        }}
        .fire-session-runtime span {{
            display: block;
            margin-top: 2px;
            color: #a7f3d0;
            font-size: 12px;
        }}
        .fire-session-clock {{
            white-space: nowrap;
            color: #bbf7d0;
            font-size: 13px;
        }}
        @media (max-width: 760px) {{
            .fire-session-runtime {{
                align-items: flex-start;
                flex-direction: column;
            }}
        }}
        </style>
        """,
        height=58,
    )
