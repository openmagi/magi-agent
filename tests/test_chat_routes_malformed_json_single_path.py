"""H-36 — the gate5b user-visible chat route's malformed-JSON path returns
unconditionally with 400 ``malformed_json`` once execution reaches the
inner ``except``. The pre-execution ``if not route_config.enabled``
guard at the top of the handler already short-circuits with 503
``python_disabled`` before the JSON parse, so a second ``if not
route_config.enabled`` inside the ``except`` was dead defensive
scaffolding.

Behavioural parity for both branches (enabled + malformed → 400;
disabled + malformed → 503 from the outer guard) is already covered
by ``tests/test_chat_route_contract.py`` and
``tests/test_streaming_chat_route.py``; this module locks the dead
branch removal so it cannot regress.
"""

from __future__ import annotations

from pathlib import Path

import magi_agent


def test_no_inner_route_config_recheck_in_chat_routes_source() -> None:
    """Meta-test: the JSON-parse ``except`` blocks inside
    ``chat_routes.py`` must not re-check ``route_config.enabled`` (the
    outer per-handler guard owns that decision). Forbid a regression
    that re-introduces the dead branch."""

    src = (
        Path(magi_agent.__file__).parent
        / "transport"
        / "chat_routes.py"
    ).read_text(encoding="utf-8")
    lines = src.splitlines()
    offenders: list[int] = []
    in_except = False
    except_indent = 0
    for idx, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("except (JSONDecodeError"):
            in_except = True
            except_indent = len(line) - len(line.lstrip())
            continue
        if in_except:
            current_indent = len(line) - len(line.lstrip())
            if stripped and current_indent <= except_indent:
                in_except = False
                # fall through so the current line is still scanned by
                # the next iteration's start-of-block check.
                continue
            # Skip comments: a docstring/comment may legitimately mention
            # the removed-pattern in prose.
            if stripped.startswith("#"):
                continue
            if "if not route_config.enabled" in stripped:
                offenders.append(idx)
    assert offenders == [], (
        "An inner ``if not route_config.enabled`` re-check has been "
        "re-introduced inside a JSON-parse ``except`` block. The outer "
        "handler guard owns that decision; the inner check is dead. "
        f"Offending lines: {offenders}"
    )
