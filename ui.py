from __future__ import annotations

from pathlib import Path

import plotly.graph_objects as go
import streamlit as st


ROOT = Path(__file__).resolve().parent


def configure_page() -> None:
    st.set_page_config(
        page_title="Al Sadi Knitwear OS",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    load_css()


def load_css() -> None:
    css = (ROOT / "styles.css").read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def sidebar_navigation(pages: list[str]) -> str:
    st.sidebar.markdown(
        """
        <div class="brand">
            <h1>Al Sadi Knitwear</h1>
            <span>Factory management console</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    selected = st.sidebar.radio("Navigation", pages, label_visibility="collapsed")
    st.sidebar.markdown("---")
    st.sidebar.markdown(
        """
        <div class="glass-card">
            <div class="metric-label">System Ready</div>
            <div class="status-chip">SQLite Connected</div>
            <p class="activity-detail" style="margin-top:.7rem">Prepared for WhatsApp, AI parsing, and PDF reporting modules.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    return selected


def page_header(title: str, subtitle: str, chip: str | None = None) -> None:
    chip_html = f'<span class="status-chip">{chip}</span>' if chip else ""
    st.markdown(
        f"""
        <div class="page-title">
            <div>
                <h2>{title}</h2>
                <p>{subtitle}</p>
            </div>
            {chip_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def metric_card(label: str, value: str, foot: str) -> None:
    st.markdown(
        f"""
        <div class="glass-card metric-card">
            <div class="metric-label">{label}</div>
            <div class="metric-value">{value}</div>
            <div class="metric-foot">{foot}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def glass_open(title: str | None = None) -> None:
    title_html = f'<div class="section-label">{title}</div>' if title else ""
    st.markdown(f'<div class="glass-card">{title_html}', unsafe_allow_html=True)


def glass_close() -> None:
    st.markdown("</div>", unsafe_allow_html=True)


def plotly_layout(fig: go.Figure, height: int = 340) -> go.Figure:
    fig.update_layout(
        autosize=True,
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#dcefff", family="Inter"),
        margin=dict(l=12, r=12, t=30, b=24),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.update_xaxes(gridcolor="rgba(125,219,255,.08)", zerolinecolor="rgba(125,219,255,.12)")
    fig.update_yaxes(gridcolor="rgba(125,219,255,.08)", zerolinecolor="rgba(125,219,255,.12)")
    return fig


def money(value: float) -> str:
    return f"BDT {value:,.0f}"
