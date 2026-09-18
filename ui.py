from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from html import escape
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
import streamlit as st

from auth import AuthenticatedUser
from database import FactoryError, MutationResult, get_excel_sync_status

ROOT = Path(__file__).resolve().parent

# Semantic chart palette. Cyan is the accent, value colour carries meaning.
CHART_COLORS = {
    "accent": "#18d7ff",
    "blue": "#3b82f6",
    "green": "#34d399",
    "red": "#f87171",
    "amber": "#fbbf24",
    "violet": "#a78bfa",
    "slate": "#94a3b8",
}
CHART_SEQUENCE = [
    "#18d7ff", "#3b82f6", "#34d399", "#fbbf24",
    "#a78bfa", "#f87171", "#94a3b8", "#22d3ee",
]

# Shared status vocabulary so machine/attendance/employee states look the
# same everywhere they appear.
STATUS_TONES = {
    "Running": "chip-running", "Active": "chip-active", "Present": "chip-present",
    "Idle": "chip-idle", "Maintenance": "chip-maintenance", "On Leave": "chip-on-leave",
    "Late": "chip-late", "Offline": "chip-offline", "Out of Service": "chip-out-of-service",
    "Absent": "chip-absent", "Inactive": "chip-inactive", "Archived": "chip-inactive",
    "Leave": "chip-on-leave",
}


def configure_page() -> None:
    st.set_page_config(
        page_title="Al Sadi Knitwear OS",
        page_icon="AS",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    load_css()


@st.cache_data(show_spinner=False)
def _css_text(mtime: float) -> str:
    """Cached on the stylesheet's mtime so edits still hot-reload."""
    return (ROOT / "styles.css").read_text(encoding="utf-8")


def load_css() -> None:
    path = ROOT / "styles.css"
    st.markdown(f"<style>{_css_text(path.stat().st_mtime)}</style>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Layout primitives
# ---------------------------------------------------------------------------

def _key(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(raw).lower()).strip("-") or "x"


@contextmanager
def card(
    title: str | None = None,
    *,
    key: str,
    note: str | None = None,
) -> Iterator[Any]:
    """A real bordered container.

    Replaces the old glass_open/glass_close pair, which emitted an unclosed
    <div> into its own stMarkdown block. The browser auto-closed that div
    immediately, so the card rendered empty and its content landed outside
    it. st.container(key=...) gives us genuine DOM nesting plus a stable
    `st-key-` class to style.
    """
    container = st.container(key=f"alsadi-card-{_key(key)}")
    with container:
        if title:
            note_html = f'<span class="card-note">{escape(note)}</span>' if note else ""
            st.markdown(
                f'<div class="card-head"><p class="card-title">{escape(title)}</p>{note_html}</div>',
                unsafe_allow_html=True,
            )
        yield container


def section_head(title: str, note: str | None = None) -> None:
    note_html = f'<span class="card-note">{escape(note)}</span>' if note else ""
    st.markdown(
        f'<div class="section-head"><h3>{escape(title)}</h3>{note_html}</div>',
        unsafe_allow_html=True,
    )


def spacer(size: str = "section") -> None:
    st.markdown(
        f'<div class="{"bottom-safe-space" if size == "bottom" else "section-spacer"}"></div>',
        unsafe_allow_html=True,
    )


def page_header(title: str, subtitle: str, chip: str | None = None, eyebrow: str | None = None) -> None:
    chip_html = f'<span class="status-chip no-dot">{escape(chip)}</span>' if chip else ""
    eyebrow_html = f'<span class="eyebrow">{escape(eyebrow)}</span>' if eyebrow else ""
    st.markdown(
        f"""
        <div class="page-title">
            <div>{eyebrow_html}
                <h2>{escape(title)}</h2>
                <p>{escape(subtitle)}</p>
            </div>
            {chip_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

NAV_GROUPS = [
    ("Overview", ["Dashboard"]),
    ("Daily Operations", ["Daily Production", "Expenses", "Employees", "Machines"]),
    ("Analysis", ["Reports"]),
    ("Administration", ["Settings"]),
]


def sidebar_navigation(
    pages: list[str], user: AuthenticatedUser
) -> tuple[str, bool, Any]:
    initials = (user.username[:2] or "?").upper()
    role_class = "role-staff" if user.role == "Staff" else "role-admin"

    st.sidebar.markdown(
        """
        <div class="brand">
            <div class="brand-mark">AS</div>
            <div>
                <h1>Al Sadi Knitwear</h1>
                <span>Factory Management OS</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.sidebar.markdown(
        f"""
        <div class="session-card">
            <div class="session-avatar">{escape(initials)}</div>
            <div>
                <strong>{escape(user.username)}</strong>
                <span class="role-tag {role_class}">{escape(user.role)}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Grouped navigation. Only groups with pages this role can reach are
    # drawn, so Staff never see an Administration heading with nothing in it.
    #
    # Buttons rather than one st.radio per group: independent radio widgets
    # each keep their own selection, so two groups could both report a
    # selected row. A single session_state value is the only source of truth.
    available = list(pages)
    selected = st.session_state.get("nav_selection", available[0])
    if selected not in available:
        selected = available[0]

    for group_name, group_pages in NAV_GROUPS:
        visible = [name for name in group_pages if name in available]
        if not visible:
            continue
        st.sidebar.markdown(f'<div class="nav-group">{escape(group_name)}</div>', unsafe_allow_html=True)
        for name in visible:
            is_active = name == selected
            clicked = st.sidebar.button(
                name,
                key=f"alsadi-nav-{_key(name)}",
                width="stretch",
                type="primary" if is_active else "tertiary",
            )
            if clicked and not is_active:
                st.session_state["nav_selection"] = name
                st.rerun()

    st.session_state["nav_selection"] = selected

    if user.role == "Staff":
        st.sidebar.caption("Staff access: view and add entries. Editing, backups and user management are Admin-only.")

    st.sidebar.markdown('<div class="section-spacer"></div>', unsafe_allow_html=True)

    # The sidebar is drawn before the page body, so an action taken while the
    # page renders would leave this chip showing the previous status - the
    # sidebar could read "Synced" beside a page reading "Failed". Reserve the
    # slot here and let the caller fill it once the page has finished.
    sync_slot = st.sidebar.empty()

    logout = st.sidebar.button("Sign out", width="stretch", key="sidebar_logout")
    return selected, logout, sync_slot


def render_sidebar_sync(slot: Any) -> None:
    """Fill the sidebar's reserved Excel chip with the current status."""
    sync = get_excel_sync_status()
    slot.markdown(
        f"""
        <div class="sync-bar {_sync_class(sync['status'])}">
            <span class="status-chip {_sync_chip(sync['status'])}">{escape(sync['status'])}</span>
            <span class="sync-bar-text">Excel</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def money(value: float) -> str:
    return f"BDT {value:,.0f}"


def compact_number(value: float) -> str:
    """Keeps large factory quantities readable inside a narrow tile."""
    value = float(value)
    for limit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(value) >= limit:
            return f"{value / limit:,.1f}{suffix}".replace(".0", "")
    return f"{value:,.0f}"


def delta_html(current: float, previous: float, *, invert: bool = False) -> str:
    """Percentage change chip. `invert` flags metrics where down is good."""
    if previous == 0:
        if current == 0:
            return '<span class="delta delta-flat">no change</span>'
        good = (current > 0) != invert
        return f'<span class="delta {"delta-up" if good else "delta-down"}">new</span>'
    change = (current - previous) / abs(previous) * 100
    if abs(change) < 0.05:
        return '<span class="delta delta-flat">0%</span>'
    good = (change > 0) != invert
    arrow = "▲" if change > 0 else "▼"
    return f'<span class="delta {"delta-up" if good else "delta-down"}">{arrow} {abs(change):,.0f}%</span>'


def kpi_tile(
    label: str,
    value: str,
    foot: str = "",
    *,
    tone: str = "accent",
    delta: str | None = None,
    progress: float | None = None,
) -> None:
    """A KPI tile. `foot` and `delta` are pre-escaped HTML-safe fragments."""
    bar = ""
    if progress is not None:
        pct = max(0.0, min(100.0, float(progress)))
        bar = f'<div class="kpi-bar"><span style="width:{pct:.0f}%"></span></div>'
    foot_bits = []
    if delta:
        foot_bits.append(delta)
    if foot:
        foot_bits.append(f"<span>{escape(foot)}</span>")
    foot_html = f'<div class="kpi-foot">{"".join(foot_bits)}</div>' if foot_bits else ""
    st.markdown(
        f"""
        <div class="kpi kpi-{tone}">
            <div class="kpi-label">{escape(label)}</div>
            <div class="kpi-value">{escape(value)}</div>
            {bar}{foot_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def status_chip(status: str) -> str:
    tone = STATUS_TONES.get(str(status), "")
    return f'<span class="status-chip {tone}">{escape(str(status))}</span>'


def empty_state(title: str, message: str, icon: str = "—") -> None:
    st.markdown(
        f"""
        <div class="empty-state">
            <div class="empty-icon">{escape(icon)}</div>
            <h4>{escape(title)}</h4>
            <p>{escape(message)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def plotly_layout(fig: go.Figure, height: int = 320) -> go.Figure:
    fig.update_layout(
        autosize=True,
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(
            color="#b3c8db",
            family='-apple-system, "Segoe UI", Roboto, sans-serif',
            size=12,
        ),
        margin=dict(l=8, r=8, t=8, b=8),
        hoverlabel=dict(
            bgcolor="#112339",
            bordercolor="rgba(125,200,255,.26)",
            font=dict(color="#e8f4fb", size=12),
        ),
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            title_text="",
            font=dict(size=11),
        ),
        colorway=CHART_SEQUENCE,
        separators=".,",
    )
    # Horizontal rules only: vertical gridlines add noise on time series.
    fig.update_xaxes(
        showgrid=False,
        zeroline=False,
        showline=True,
        linecolor="rgba(125,200,255,.14)",
        ticks="outside",
        tickcolor="rgba(125,200,255,.14)",
        ticklen=4,
        title_text="",
    )
    fig.update_yaxes(
        gridcolor="rgba(125,200,255,.08)",
        zeroline=True,
        zerolinecolor="rgba(125,200,255,.2)",
        showline=False,
        title_text="",
    )
    return fig


def chart(fig: go.Figure, height: int = 320, *, key: str | None = None) -> None:
    st.plotly_chart(
        plotly_layout(fig, height),
        width="stretch",
        key=key,
        config={"displayModeBar": False, "responsive": True},
    )


# ---------------------------------------------------------------------------
# Excel sync visibility
# ---------------------------------------------------------------------------

def _sync_class(status: str) -> str:
    return {
        "Synced": "is-synced",
        "Out of date": "is-out-of-date",
        "Failed": "is-failed",
    }.get(status, "is-out-of-date")


def _sync_chip(status: str) -> str:
    return {
        "Synced": "sync-synced",
        "Out of date": "sync-out-of-date",
        "Failed": "sync-failed",
    }.get(status, "sync-out-of-date")


def retry_excel_sync() -> bool:
    """Re-attempt the workbook rebuild.

    SQLite is already authoritative and committed by the time this runs;
    this only retries the post-commit Excel step, exactly as the Reports
    page does. A failure here never affects saved data.
    """
    from database import set_excel_sync_status
    from spreadsheet_sync import sync_factory_workbook

    try:
        path = sync_factory_workbook()
        set_excel_sync_status("Synced", f"Workbook updated: {path.name}")
        return True
    except PermissionError:
        set_excel_sync_status(
            "Failed",
            "Close factory_records.xlsx in Excel, then retry the spreadsheet sync.",
        )
        return False
    except Exception as exc:
        set_excel_sync_status("Failed", f"Spreadsheet sync failed: {exc}")
        return False


def sync_bar(*, allow_retry: bool = True, key: str = "page") -> dict[str, Any]:
    """Persistent Excel sync state, shown on every page that writes data.

    Previously this lived only in Settings and Reports, so an operator could
    work all day against a stale workbook without noticing.
    """
    status = get_excel_sync_status()
    state = status["status"]
    if state == "Synced":
        detail = f"Workbook current as of {status['last_success_at'] or 'this session'}."
    else:
        detail = status["message"] or "The workbook is behind the database."

    show_retry = allow_retry and state != "Synced"
    columns = st.columns([1, 0.16], vertical_alignment="center") if show_retry else [st.container()]
    with columns[0]:
        st.markdown(
            f"""
            <div class="sync-bar {_sync_class(state)}">
                <span class="status-chip {_sync_chip(state)}">{escape(state)}</span>
                <span class="sync-bar-text"><strong>Excel sync</strong> &middot; {escape(detail)}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    if show_retry:
        with columns[1]:
            if st.button("Retry sync", key=f"retry_sync_{_key(key)}", width="stretch"):
                with st.spinner("Rebuilding workbook..."):
                    ok = retry_excel_sync()
                if ok:
                    st.toast("Excel workbook is back in sync.", icon="✅")
                st.rerun()
    return status


def sync_status_panel() -> dict[str, Any]:
    """Detailed sync panel for Settings and Reports."""
    status = get_excel_sync_status()
    st.markdown(
        f"""
        <div class="sync-panel">
            <div>
                <span class="status-chip {_sync_chip(status['status'])}">{escape(status['status'])}</span>
                <strong>Excel Sync Status</strong>
            </div>
            <p>{escape(status['message'] or 'No sync message.')}</p>
            <small>Last successful sync: {escape(str(status['last_success_at'] or 'Never'))}</small>
        </div>
        """,
        unsafe_allow_html=True,
    )
    return status


# ---------------------------------------------------------------------------
# Result and error feedback
# ---------------------------------------------------------------------------

FLASH_KEY = "_alsadi_flash"
NOTICE_KEY = "_alsadi_notice"


def show_mutation_result(result: MutationResult, success_message: str) -> None:
    """Confirm the SQLite commit first, then report the Excel step separately.

    The two are deliberately distinct: the save has already succeeded and is
    never rolled back by a failed workbook rebuild, so the Excel outcome is
    shown as a follow-up, not as a failure of the save.
    """
    st.toast(success_message, icon="✅")
    if result.sync_status == "Synced":
        st.success(f"{success_message} Excel workbook updated.", icon="✅")
        return
    st.success(f"{success_message} Saved to the database.", icon="✅")
    if result.sync_status == "Failed":
        st.error(f"Excel sync failed - your data is safe in the database. {result.sync_message}", icon="⚠")
    else:
        st.warning(f"Excel workbook is out of date. {result.sync_message}", icon="⚠")


def flash_mutation(result: MutationResult, success_message: str) -> None:
    """Record the outcome, then rerun so the page re-reads the database.

    Pages query their tables near the top of `render()`, before the form
    submit handler further down has run, so a freshly inserted row is not in
    the DataFrame that was already fetched this pass. Without a rerun the
    operator sees "saved" above a ledger that does not contain the row.

    Calling `st.rerun()` directly after `show_mutation_result()` does not
    work either: the rerun discards the render that just drew the message,
    so the Excel "Out of date"/"Failed" warning never reaches the screen.
    Stashing the result and drawing it at the top of the next run fixes
    both - the data is refetched *and* the message survives.

    This never affects durability. The SQLite commit has already happened by
    the time the MutationResult exists.
    """
    st.session_state[FLASH_KEY] = (
        success_message,
        result.sync_status,
        result.sync_message,
    )
    st.rerun()


_NOTICE_RENDERERS = {
    "success": (st.success, "✅"),
    "warning": (st.warning, "⚠"),
    "error": (st.error, "⚠"),
}


def show_flash() -> None:
    """Draw and clear a pending flash from the previous run, if any."""
    note = st.session_state.pop(NOTICE_KEY, None)
    if note:
        # Tolerate the bare-string form a session may still be holding from
        # before tones existed.
        message, tone = note if isinstance(note, tuple) else (note, "success")
        render, icon = _NOTICE_RENDERERS.get(tone, _NOTICE_RENDERERS["success"])
        render(message, icon=icon)

    payload = st.session_state.pop(FLASH_KEY, None)
    if not payload:
        return
    success_message, sync_status, sync_message = payload
    show_mutation_result(MutationResult(0, sync_status, sync_message), success_message)


def flash_notice(message: str, tone: str = "success") -> None:
    """Report a non-mutation action across an immediate rerun.

    Same problem as flash_mutation: `st.success(...)` followed by
    `st.rerun()` draws the message and then discards that render, so the
    operator never sees it.

    The rerun matters for more than the message. Anything the action changed
    that the page already rendered above it - the Excel sync panel, a backup
    listing - is re-read on the next pass, so the page cannot show a stale
    status beside a fresh confirmation.

    `tone` selects success/warning/error styling, so a failed action can use
    the same mechanism instead of an st.error() that a rerun would discard.
    """
    st.session_state[NOTICE_KEY] = (message, tone)
    st.rerun()


def factory_error_message(error: Exception) -> str:
    """Validation problems are the operator's to fix, so they are shown
    verbatim. Anything else is a system fault and gets a generic message that
    does not leak internals."""
    if isinstance(error, FactoryError):
        return str(error)
    return "The operation could not be completed. Check the application logs and try again."


def show_factory_error(error: Exception) -> None:
    st.error(factory_error_message(error), icon="⚠")


def flash_factory_error(error: Exception) -> None:
    """Report a failure across a rerun.

    Used where the failed action already changed something the page rendered
    above it - most importantly the Excel sync status, which would otherwise
    still read "Synced" directly above a message saying the sync failed.
    """
    flash_notice(factory_error_message(error), tone="error")


def field_error(message: str) -> None:
    st.markdown(f'<div class="field-error">{escape(message)}</div>', unsafe_allow_html=True)


def form_total(label: str, value: str) -> None:
    st.markdown(
        f'<div class="form-total"><span class="label">{escape(label)}</span>'
        f'<span class="value">{escape(value)}</span></div>',
        unsafe_allow_html=True,
    )
