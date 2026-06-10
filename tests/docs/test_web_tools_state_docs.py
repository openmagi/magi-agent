"""Guards docs/tools.md + docs/what-works-today.md web-tools state (02-PR3, D1/D5).

The PR-3 review (02-web-live-search.md spec, file-map line 178-179) flagged three
gaps in the original docs commit (b26f516f):

- The First-party tool catalog table follows a ``Permission`` column convention
  (read / write / execute / meta). ``WebSearch`` / ``WebFetch`` were added only as
  a prose section, so their ``net`` permission classification — required by the
  spec file-map (``permission=net``) — was never stated. A catalog-style row with
  ``net`` must appear.

- The spec file-map and the work order asked for a ``WebReader`` row. Open
  decision #3 (spec line 203) resolves WebReader as **out-of-scope**: the native
  plugin catalog (``magi_agent/plugins/native/web.py``) exposes only ``WebSearch``
  and ``WebFetch`` — there is no ``WebReader`` handler. The honest move is to say
  so explicitly rather than silently omit it.

- The what-works-today web bullet must footnote the real source paths
  (``magi_agent/plugins/native/web.py`` and
  ``magi_agent/web_acquisition/research_tools.py``) per spec line 179, not only a
  ``/docs/tools`` link.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "docs" / "tools.md"
WHAT_WORKS = ROOT / "docs" / "what-works-today.md"


def _tools_text() -> str:
    return TOOLS.read_text(encoding="utf-8")


def _what_works_text() -> str:
    return WHAT_WORKS.read_text(encoding="utf-8")


def test_websearch_webfetch_carry_net_permission() -> None:
    text = _tools_text()
    # Catalog-style rows classifying both tools with the `net` permission,
    # matching the table's Permission-column convention.
    assert "| `WebSearch` |" in text
    assert "| `WebFetch` |" in text
    # `net` permission must be stated for the web tools (spec: permission=net).
    websearch_line = next(
        line for line in text.splitlines() if line.startswith("| `WebSearch` |")
    )
    webfetch_line = next(
        line for line in text.splitlines() if line.startswith("| `WebFetch` |")
    )
    assert "net" in websearch_line
    assert "net" in webfetch_line


def test_webreader_out_of_scope_is_explicit() -> None:
    text = _tools_text()
    # The native plugin exposes only WebSearch/WebFetch; WebReader is not a
    # registered handler. The doc must say so rather than silently omitting it.
    assert "WebReader" in text
    lowered = text.lower()
    assert "not exposed" in lowered or "out of scope" in lowered or "no webreader" in lowered


def test_what_works_web_bullet_footnotes_source_paths() -> None:
    text = _what_works_text()
    assert "magi_agent/plugins/native/web.py" in text
    assert "magi_agent/web_acquisition/research_tools.py" in text


def test_honest_not_configured_contract_preserved() -> None:
    # Regression guard: the honest-error posture from PR-1 must remain documented.
    assert "web_research_not_configured" in _tools_text()
    assert "web_research_not_configured" in _what_works_text()
