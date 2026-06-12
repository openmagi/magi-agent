"""BUG 3 — the validator gate must NOT fail-OPEN on an unrelated pack import error.

Validator refs are declared STATICALLY in ``pack.toml`` manifests. But
``_loaded_pack_validator_refs`` called ``load_packs(enabled, RecordingSink())`` —
which lazily IMPORTS every enabled pack's impl just to build the catalog — and the
broad ``except Exception: return ()`` meant ANY enabled pack with an import-time
failure (e.g. a tool pack importing a package the user lacks) silently dropped ALL
pack validator refs from the gate's ``requiredValidators``. That is fail-OPEN: the
enforcement gate is silently disabled by an unrelated pack, the worst failure mode
for a safety gate.

The refs are static manifest data; reading them must not require importing any
impl. Realistic scenario: a user has BOTH a validator pack (declares
``verifier:userQuote@1``) and an unrelated tool pack whose impl raises on import —
the validator must still reach the gate.
"""
from __future__ import annotations

from pathlib import Path

import magi_agent.packs.discovery as discovery
from magi_agent.cli.real_runner import _loaded_pack_validator_refs

_VALIDATOR_REF = "verifier:userQuote@1"

# An impl module that explodes at IMPORT time — exactly like a user tool pack that
# imports a package they never installed.
_BROKEN_IMPL = "import this_module_does_not_exist_anywhere_xyz  # noqa: F401\n"


def _write_validator_pack(root: Path) -> None:
    pack_dir = root / "quote_validator"
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "__init__.py").write_text("")
    # A real, importable validator impl (it would import fine — but the fix means
    # we never even import it to read its statically-declared ref).
    (pack_dir / "impl.py").write_text(
        "from magi_agent.packs.context import ValidatorVerdict\n"
        "def provide_quote(ctx):\n"
        "    ctx.emit(passed=True)\n"
    )
    (pack_dir / "pack.toml").write_text(
        'packId = "user.quote"\n'
        'displayName = "Quote validator"\n'
        'version = "0.0.1"\n\n'
        "[[provides]]\n"
        'type = "validator"\n'
        f'ref = "{_VALIDATOR_REF}"\n'
        'impl = "quote_validator.impl:provide_quote"\n'
    )


def _write_broken_tool_pack(root: Path) -> None:
    pack_dir = root / "broken_tool"
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "impl.py").write_text(_BROKEN_IMPL)
    (pack_dir / "pack.toml").write_text(
        'packId = "user.broken-tool"\n'
        'displayName = "Broken tool"\n'
        'version = "0.0.1"\n\n'
        "[[provides]]\n"
        'type = "tool"\n'
        'ref = "BrokenTool"\n'
        'impl = "broken_tool.impl:provide_broken"\n'
    )


def test_validator_ref_survives_unrelated_pack_import_error(
    tmp_path: Path, monkeypatch
) -> None:
    base = tmp_path / "packs"
    _write_validator_pack(base)
    _write_broken_tool_pack(base)
    monkeypatch.syspath_prepend(str(base))

    config_path = tmp_path / "config.toml"
    config_path.write_text("[packs]\n")
    monkeypatch.setenv("MAGI_CONFIG", str(config_path))

    # Both packs are discovered + enabled. The broken tool pack's impl raises on
    # import; the validator ref is static manifest data and must still reach here.
    monkeypatch.setattr(discovery, "default_search_bases", lambda: [base])

    refs = _loaded_pack_validator_refs()
    assert _VALIDATOR_REF in refs, refs
