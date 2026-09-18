"""Static checks on the Streamlit layer.

The test suite never renders Streamlit, so a call that is only rejected at
render time - a bad icon, a missing flash handler - passes every other test
and still breaks the app the moment an operator uses it. These walk the AST
instead, which is cheap and catches exactly that class of defect.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from streamlit.string_util import validate_emoji

ROOT = Path(__file__).resolve().parent.parent
UI_FILES = sorted([*ROOT.glob("*.py"), *(ROOT / "pages").glob("*.py")])

# Streamlit validates the icon= argument on these and raises
# StreamlitAPIException for anything that is not a real emoji. U+2713 CHECK
# MARK looks like a tick but is a dingbat, and is rejected.
ICON_VALIDATING_CALLS = {
    "toast", "success", "error", "warning", "info", "exception", "badge",
}


def _streamlit_calls(tree: ast.AST):
    """Yield (call, attribute_name) for every `st.<name>(...)` call."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "st"
        ):
            yield node, func.attr


def _const_kwarg(call: ast.Call, name: str) -> str | None:
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


@pytest.mark.parametrize("path", UI_FILES, ids=lambda p: p.name)
def test_streamlit_icons_are_valid_emoji(path: Path):
    """Every icon= passed to a Streamlit call must survive validate_emoji.

    Regression test: st.toast(..., icon="✓") raised
    StreamlitAPIException on every successful save, which no other test
    could see because none of them render.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    problems = []
    for call, attr in _streamlit_calls(tree):
        if attr not in ICON_VALIDATING_CALLS:
            continue
        icon = _const_kwarg(call, "icon")
        if icon is None:
            continue
        try:
            validate_emoji(icon)
        except Exception as exc:
            codepoints = " ".join(f"U+{ord(c):04X}" for c in icon)
            problems.append(f"{path.name}:{call.lineno} st.{attr}(icon=...) {codepoints}: {exc}")

    assert not problems, "Invalid Streamlit icons:\n" + "\n".join(problems)


def test_every_page_renders_pending_flashes():
    """A page that flashes must also draw flashes.

    flash_mutation()/flash_notice() stash a message and rerun. If the page
    never calls show_flash(), the message is stored and silently dropped on
    the next pass - the exact failure the flash pattern exists to prevent.
    """
    offenders = []
    for path in (ROOT / "pages").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        flashes = "flash_mutation(" in source or "flash_notice(" in source
        draws = "show_flash()" in source
        if flashes and not draws:
            offenders.append(path.name)

    # telegram_admin renders inside a Settings tab, so Settings draws for it.
    offenders = [name for name in offenders if name != "telegram_admin.py"]
    assert not offenders, (
        "These pages stash flash messages but never call show_flash(), "
        f"so the message is dropped: {offenders}"
    )


def test_mutation_pages_surface_excel_sync_state():
    """Excel sync status must be visible on every page that writes data.

    The guarantee in the README is that a save always commits to SQLite and
    the Excel step is reported separately. That reporting only works if the
    page actually shows the status.
    """
    missing = []
    for name in ["production.py", "expenses.py", "machines.py", "employees.py"]:
        source = (ROOT / "pages" / name).read_text(encoding="utf-8")
        if "sync_bar(" not in source:
            missing.append(name)
    assert not missing, f"Mutation pages with no Excel sync indicator: {missing}"
