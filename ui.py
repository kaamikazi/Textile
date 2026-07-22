from __future__ import annotations

from pathlib import Path
from typing import Any

import plotly.graph_objects as go
import streamlit as st

from auth import AuthenticatedUser
from database import FactoryError, MutationResult, get_excel_sync_status


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


def sidebar_navigation(pages: list[str], user: AuthenticatedUser) -> tuple[str, bool]:
    st.sidebar.markdown(
        """
        <div class="brand">
            <h1>Al Sadi Knitwear</h1>
            <span>Factory management console</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.sidebar.markdown(
        f"""
        <div class="session-card">
            <strong>{user.username}</strong>
            <span>{user.role} | Local session</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    selected = st.sidebar.radio("Navigation", pages, label_visibility="collapsed")
    st.sidebar.markdown("---")
    sync = get_excel_sync_status()
    status_class = sync["status"].lower().replace(" ", "-")
    st.sidebar.markdown(
        f"""
        <div class="glass-card">
            <div class="metric-label">Excel Sync Status</div>
            <div class="status-chip sync-{status_class}">{sync['status']}</div>
            <p class="activity-detail" style="margin-top:.7rem">Last success: {sync['last_success_at'] or 'Not yet synced'}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    logout = st.sidebar.button("Logout", width="stretch")
    return selected, logout


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


def show_mutation_result(result: MutationResult, success_message: str) -> None:
    st.success(success_message)
    if result.sync_status != "Synced":
        st.warning(result.sync_message)


def show_factory_error(error: Exception) -> None:
    if isinstance(error, FactoryError):
        st.error(str(error))
    else:
        st.error("The operation could not be completed. Check the application logs and try again.")


def sync_status_panel() -> dict[str, Any]:
    status = get_excel_sync_status()
    style = {
        "Synced": "sync-synced",
        "Out of date": "sync-out-of-date",
        "Failed": "sync-failed",
    }.get(status["status"], "sync-out-of-date")
    st.markdown(
        f"""
        <div class="sync-panel">
            <div>
                <span class="status-chip {style}">{status['status']}</span>
                <strong>Excel Sync Status</strong>
            </div>
            <p>{status['message'] or 'No sync message.'}</p>
            <small>Last successful sync: {status['last_success_at'] or 'Never'}</small>
        </div>
        """,
        unsafe_allow_html=True,
    )
    return status
