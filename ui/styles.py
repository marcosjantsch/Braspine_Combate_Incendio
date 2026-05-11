# -*- coding: utf-8 -*-
from __future__ import annotations

import streamlit as st


def apply_styles() -> None:
    st.markdown(
        """
        <style>
        .stApp {
            background: linear-gradient(180deg, #020617 0%, #030712 100%);
            color: #e5fff3;
        }
        [data-testid="stHeader"] { background: rgba(0,0,0,0); }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, rgba(2,6,23,0.96) 0%, rgba(3,12,10,0.98) 100%);
            border-right: 1px solid rgba(16,185,129,0.20);
        }
        .block-container {
            max-width: 100%;
            padding-top: 1rem;
            padding-bottom: 1rem;
        }
        .fire-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 18px;
            margin-bottom: 14px;
            padding: 18px 20px;
            border: 1px solid rgba(16,185,129,0.22);
            border-radius: 18px;
            background:
                radial-gradient(circle at top right, rgba(16,185,129,0.12), transparent 30%),
                linear-gradient(135deg, rgba(2,6,23,0.95), rgba(6,78,59,0.58));
            box-shadow: 0 20px 45px rgba(0,0,0,0.24);
        }
        .fire-title {
            margin: 0;
            color: #ecfdf5;
            font-size: 24px;
            font-weight: 850;
            letter-spacing: 0;
        }
        .fire-subtitle {
            margin-top: 4px;
            color: #a7f3d0;
            font-size: 13px;
        }
        .fire-session {
            color: #d1fae5;
            font-size: 12px;
            text-align: right;
        }
        [data-testid="stSidebar"] .stTabs [data-baseweb="tab"] {
            background: rgba(15, 23, 42, 0.85);
            color: #d1fae5;
            border: 1px solid rgba(16, 185, 129, 0.16);
            border-radius: 10px 10px 0 0;
            font-size: 12px;
            font-weight: 800;
        }
        [data-testid="stSidebar"] .stTabs [aria-selected="true"] {
            background: rgba(5, 150, 105, 0.18);
            color: #a7f3d0;
            border-color: rgba(52, 211, 153, 0.35);
        }
        .stButton > button,
        .stDownloadButton > button {
            background: linear-gradient(180deg, #052e1f 0%, #064e3b 100%);
            color: #ecfdf5;
            border: 1px solid rgba(52, 211, 153, 0.35);
            border-radius: 12px;
            font-weight: 800;
        }
        .stButton > button:hover,
        .stDownloadButton > button:hover {
            border-color: rgba(110, 231, 183, 0.55);
            color: #bbf7d0;
        }
        div[data-baseweb="select"] > div,
        div[data-baseweb="input"] > div,
        .stNumberInput > div > div,
        .stTextInput > div > div {
            background: rgba(3, 7, 18, 0.92) !important;
            color: #ecfdf5 !important;
            border: 1px solid rgba(16, 185, 129, 0.24) !important;
        }
        .stTextInput label,
        .stNumberInput label,
        .stMarkdown,
        .stCaption,
        .stSubheader,
        label {
            color: #d1fae5 !important;
        }
        [data-testid="stInfo"],
        [data-testid="stSuccess"],
        [data-testid="stWarning"],
        [data-testid="stError"] {
            background: rgba(3, 7, 18, 0.90);
            color: #ecfdf5;
            border: 1px solid rgba(52, 211, 153, 0.24);
        }
        iframe {
            border-radius: 16px;
            border: 1px solid rgba(16, 185, 129, 0.20);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
