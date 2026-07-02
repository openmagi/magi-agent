"""S-05: the gate-readiness digest primitives live in one shared leaf.

``_sha256_text_digest`` / ``_digest_present`` / ``_DIGEST_RE`` were copy-pasted
verbatim into all ten ``*_readiness.py`` modules. A change to the digest scheme
in one (e.g. tightening the hex length) would silently leave the other nine
matching the old format. This pins the consolidation: every readiness module
references the SAME ``gates._readiness_common`` objects.
"""

from __future__ import annotations

import importlib
import re
from types import SimpleNamespace

import pytest

from magi_agent.gates import _readiness_common as common


_READINESS_MODULES = (
    "gate2_readiness",
    "gate3_readiness",
    "gate4_readiness",
    "gate5_readiness",
    "gate7_readiness",
    "gate8_readiness",
    "learning_live_readiness",
    "memory_write_readiness",
    "scheduler_executor_readiness",
    "workflow_executor_readiness",
)


def _module(name: str):
    return importlib.import_module(f"magi_agent.gates.{name}")


@pytest.mark.parametrize("name", _READINESS_MODULES)
def test_modules_share_kernel_digest_helpers(name: str) -> None:
    mod = _module(name)
    assert mod._sha256_text_digest is common.sha256_text_digest, (
        f"{name} must use the shared sha256_text_digest, not a local fork"
    )
    assert mod._digest_present is common.digest_present
    assert mod._DIGEST_RE is common.DIGEST_RE


@pytest.mark.parametrize("name", _READINESS_MODULES)
def test_modules_share_kernel_scope_matcher(name: str) -> None:
    """G6: every readiness module references the SAME shared scope matcher and
    the SAME ``SAFE_ENVIRONMENTS`` frozenset, not a local fork."""
    mod = _module(name)
    assert mod._selected_scope_matched is common.selected_scope_matched, (
        f"{name} must alias the shared selected_scope_matched, not a local fork"
    )
    assert mod._SAFE_ENVIRONMENTS is common.SAFE_ENVIRONMENTS, (
        f"{name} must alias the shared SAFE_ENVIRONMENTS, not a local fork"
    )


def test_no_readiness_module_redefines_the_scope_matcher() -> None:
    """Ratchet: no ``*_readiness.py`` may carry its own ``def _selected_scope_matched``
    (it must alias the shared one)."""
    import pathlib

    gates_dir = pathlib.Path(common.__file__).parent
    offenders: list[str] = []
    for path in gates_dir.glob("*_readiness.py"):
        src = path.read_text(encoding="utf-8")
        if re.search(r"^def _selected_scope_matched\b", src, re.MULTILINE):
            offenders.append(path.name)
    assert not offenders, (
        "These readiness modules still define the scope matcher locally instead "
        "of aliasing magi_agent.gates._readiness_common.selected_scope_matched: "
        + ", ".join(sorted(offenders))
    )


def _scoped_config(
    *,
    enabled: bool = True,
    selected_bot_digest: str,
    selected_owner_user_id_digest: str,
    environment: str = "production",
    environment_allowlist: tuple[str, ...] = ("production",),
) -> SimpleNamespace:
    return SimpleNamespace(
        enabled=enabled,
        selected_bot_digest=selected_bot_digest,
        selected_owner_user_id_digest=selected_owner_user_id_digest,
        environment=environment,
        environment_allowlist=environment_allowlist,
    )


def test_selected_scope_matched_behavior_table() -> None:
    bot_id = "bot-123"
    user_id = "user-456"
    bot_digest = common.sha256_text_digest(bot_id)
    owner_digest = common.sha256_text_digest(user_id)

    # 1. disabled -> False
    cfg = _scoped_config(
        enabled=False,
        selected_bot_digest=bot_digest,
        selected_owner_user_id_digest=owner_digest,
    )
    assert common.selected_scope_matched(cfg, bot_id=bot_id, user_id=user_id) is False

    # 2. malformed digest format -> False
    cfg = _scoped_config(
        selected_bot_digest="not-a-digest",
        selected_owner_user_id_digest=owner_digest,
    )
    assert common.selected_scope_matched(cfg, bot_id=bot_id, user_id=user_id) is False

    # 3. bot digest mismatch -> False
    cfg = _scoped_config(
        selected_bot_digest=common.sha256_text_digest("other-bot"),
        selected_owner_user_id_digest=owner_digest,
    )
    assert common.selected_scope_matched(cfg, bot_id=bot_id, user_id=user_id) is False

    # 4. owner digest mismatch -> False
    cfg = _scoped_config(
        selected_bot_digest=bot_digest,
        selected_owner_user_id_digest=common.sha256_text_digest("other-user"),
    )
    assert common.selected_scope_matched(cfg, bot_id=bot_id, user_id=user_id) is False

    # 5. unsafe environment -> False
    cfg = _scoped_config(
        selected_bot_digest=bot_digest,
        selected_owner_user_id_digest=owner_digest,
        environment="wonderland",
        environment_allowlist=("wonderland",),
    )
    assert common.selected_scope_matched(cfg, bot_id=bot_id, user_id=user_id) is False

    # 6. environment not in allowlist -> False
    cfg = _scoped_config(
        selected_bot_digest=bot_digest,
        selected_owner_user_id_digest=owner_digest,
        environment="production",
        environment_allowlist=("staging",),
    )
    assert common.selected_scope_matched(cfg, bot_id=bot_id, user_id=user_id) is False

    # 7. all pass -> True
    cfg = _scoped_config(
        selected_bot_digest=bot_digest,
        selected_owner_user_id_digest=owner_digest,
        environment="production",
        environment_allowlist=("production",),
    )
    assert common.selected_scope_matched(cfg, bot_id=bot_id, user_id=user_id) is True


def test_safe_environments_membership() -> None:
    assert common.SAFE_ENVIRONMENTS == frozenset(
        {"local", "development", "staging", "production"}
    )


def test_sha256_text_digest_format() -> None:
    out = common.sha256_text_digest("bot-123")
    assert out.startswith("sha256:")
    assert common.DIGEST_RE.fullmatch(out)
    # deterministic
    assert out == common.sha256_text_digest("bot-123")


def test_digest_present_validates_shape() -> None:
    assert common.digest_present(common.sha256_text_digest("x")) is True
    assert common.digest_present("sha256:" + "a" * 64) is True
    assert common.digest_present("sha256:short") is False
    assert common.digest_present("plain-text") is False
    assert common.digest_present(None) is False
    assert common.digest_present(12345) is False


def test_no_readiness_module_redefines_the_digest_helpers() -> None:
    """Ratchet: no ``*_readiness.py`` may carry its own ``def _sha256_text_digest``
    or ``def _digest_present`` (it must import the shared one)."""
    import pathlib

    gates_dir = pathlib.Path(common.__file__).parent
    offenders: list[str] = []
    for path in gates_dir.glob("*_readiness.py"):
        src = path.read_text(encoding="utf-8")
        if re.search(r"^def _sha256_text_digest\b", src, re.MULTILINE) or re.search(
            r"^def _digest_present\b", src, re.MULTILINE
        ):
            offenders.append(path.name)
    assert not offenders, (
        "These readiness modules still define the digest helpers locally instead "
        "of importing magi_agent.gates._readiness_common: " + ", ".join(sorted(offenders))
    )
