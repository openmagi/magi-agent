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
