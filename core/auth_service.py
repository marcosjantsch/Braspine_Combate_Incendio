# -*- coding: utf-8 -*-
from __future__ import annotations

from html import escape
from typing import Dict, Optional

import bcrypt
import streamlit as st
import yaml

from core.config import APP_TITLE, AUTH_CONFIG_PATH


@st.cache_data(show_spinner=False)
def load_auth_config() -> Dict:
    if not AUTH_CONFIG_PATH.exists():
        raise FileNotFoundError(
            "Arquivo de autenticacao nao encontrado. Configure APP_AUTH_CONFIG "
            "ou monte auth/config.yaml no ambiente de execucao."
        )
    config = yaml.safe_load(AUTH_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(config, dict):
        raise ValueError("Arquivo de autenticacao invalido.")
    return config


def normalize_bcrypt_hash(hash_value: str) -> bytes:
    return str(hash_value).replace("$2y$", "$2b$", 1).encode("utf-8")


def verify_credentials(username: str, password: str) -> Optional[Dict]:
    config = load_auth_config()
    users = config.get("credentials", {}).get("usernames", {})
    normalized_username = username.strip().lower()
    matched_key = next((key for key in users if key.lower() == normalized_username), None)
    if not matched_key:
        return None

    profile = users[matched_key]
    password_hash = profile.get("password")
    if not password_hash:
        return None

    if bcrypt.checkpw(password.encode("utf-8"), normalize_bcrypt_hash(password_hash)):
        return {
            "username": matched_key,
            "name": profile.get("name") or matched_key,
            "role": profile.get("role") or "user",
            "email": profile.get("email") or "",
        }
    return None


def render_login_styles() -> None:
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] {
            display: none !important;
        }
        [data-testid="stHeader"] {
            background: transparent;
        }
        .block-container {
            max-width: 1180px !important;
            padding-top: 4.5rem !important;
            padding-bottom: 2.5rem !important;
        }
        .fire-login-copy,
        [data-testid="stVerticalBlockBorderWrapper"] {
            border: 1px solid rgba(52, 211, 153, 0.20);
            border-radius: 16px;
            box-shadow: 0 28px 70px rgba(0, 0, 0, 0.34);
        }
        .fire-login-copy {
            min-height: 540px;
            padding: 42px;
            background:
                linear-gradient(135deg, rgba(2, 6, 23, 0.94), rgba(6, 78, 59, 0.60)),
                repeating-linear-gradient(90deg, rgba(255,255,255,0.03) 0 1px, transparent 1px 44px),
                repeating-linear-gradient(0deg, rgba(255,255,255,0.025) 0 1px, transparent 1px 44px);
        }
        .fire-login-badge {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 7px 11px;
            border-radius: 999px;
            color: #bbf7d0;
            background: rgba(5, 150, 105, 0.14);
            border: 1px solid rgba(52, 211, 153, 0.22);
            font-size: 12px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }
        .fire-login-copy h1 {
            margin: 24px 0 16px 0;
            color: #f8fafc;
            font-size: 42px;
            line-height: 1.05;
            letter-spacing: 0;
        }
        .fire-login-copy p {
            max-width: 720px;
            margin: 0 0 22px 0;
            color: #cbd5e1;
            font-size: 16px;
            line-height: 1.65;
        }
        .fire-login-points {
            display: grid;
            gap: 12px;
            margin-top: 28px;
        }
        .fire-login-point {
            padding: 14px 16px;
            border-radius: 12px;
            background: rgba(2, 6, 23, 0.62);
            border: 1px solid rgba(148, 163, 184, 0.16);
        }
        .fire-login-point strong {
            display: block;
            margin-bottom: 4px;
            color: #d1fae5;
            font-size: 14px;
        }
        .fire-login-point span {
            color: #94a3b8;
            font-size: 13px;
            line-height: 1.45;
        }
        [data-testid="stVerticalBlockBorderWrapper"] {
            min-height: 540px;
            background: linear-gradient(180deg, rgba(15, 23, 42, 0.98), rgba(2, 6, 23, 0.98));
        }
        [data-testid="stVerticalBlockBorderWrapper"] [data-testid="stVerticalBlock"] {
            gap: 1rem;
        }
        .fire-login-heading h2 {
            margin: 0 0 8px 0;
            color: #f8fafc;
            font-size: 24px;
            letter-spacing: 0;
        }
        .fire-login-heading p {
            margin: 0 0 20px 0;
            color: #94a3b8;
            font-size: 13px;
            line-height: 1.5;
        }
        .fire-login-runtime {
            margin-top: 18px;
            padding: 12px 14px;
            border-radius: 12px;
            background: rgba(16, 185, 129, 0.09);
            border: 1px solid rgba(52, 211, 153, 0.18);
            color: #a7f3d0;
            font-size: 12px;
            line-height: 1.45;
        }
        @media (max-width: 900px) {
            .block-container {
                padding-top: 2rem !important;
            }
            .fire-login-copy,
            [data-testid="stVerticalBlockBorderWrapper"] {
                min-height: auto;
            }
            .fire-login-copy {
                padding: 28px;
            }
            .fire-login-copy h1 {
                font-size: 32px;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_platform_intro() -> None:
    title = escape(APP_TITLE)
    st.markdown(
        f"""
        <div class="fire-login-copy">
            <div class="fire-login-badge">Monitoramento florestal</div>
            <h1>{title}</h1>
            <p>
                Ambiente operacional para acompanhar areas rurais e florestais,
                combinando perimetros de empresas, camadas orbitais, dados
                climaticos e indicadores de focos de calor em uma leitura unica
                para apoio ao combate a incendios.
            </p>
            <div class="fire-login-points">
                <div class="fire-login-point">
                    <strong>Mapa operacional</strong>
                    <span>Visualizacao das empresas selecionadas, ROI de analise,
                    camadas de satelite e dados ambientais disponiveis.</span>
                </div>
                <div class="fire-login-point">
                    <strong>Risco e deteccoes</strong>
                    <span>Painel com grau de risco, focos de calor, hotspots,
                    anomalias termicas e distancias ate as fazendas monitoradas.</span>
                </div>
                <div class="fire-login-point">
                    <strong>Execucao local, Codebook e container</strong>
                    <span>Configuracoes sensiveis sao carregadas pelo ambiente,
                    mantendo o mesmo codigo pronto para operacao local e publicacao.</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_login() -> None:
    render_login_styles()
    left_col, right_col = st.columns([1.25, 0.85], gap="large")
    with left_col:
        render_platform_intro()
    with right_col:
        with st.container(border=True):
            st.markdown(
                """
                <div class="fire-login-heading">
                    <h2>Acesso seguro</h2>
                    <p>Entre com o usuario e a senha cadastrados para este ambiente.</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
            notice = st.session_state.pop("auth_notice", None)
            if notice:
                st.warning(str(notice))
            with st.form("login_form"):
                username = st.text_input("Usuario")
                password = st.text_input("Senha", type="password")
                submitted = st.form_submit_button("Entrar", use_container_width=True)
            if submitted:
                try:
                    profile = verify_credentials(username, password)
                except Exception as exc:
                    st.error(f"Falha ao carregar autenticacao: {exc}")
                    st.stop()
                if not profile:
                    st.error("Usuario ou senha invalidos.")
                    st.stop()
                st.session_state["auth_user"] = profile
                st.rerun()
            st.markdown(
                """
                <div class="fire-login-runtime">
                    Preparado para execucao local, Codebook e container, usando as
                    credenciais configuradas no ambiente da aplicacao.
                </div>
                """,
                unsafe_allow_html=True,
            )
    st.stop()


def require_authentication() -> Dict:
    profile = st.session_state.get("auth_user")
    if not profile:
        render_login()
    return profile
