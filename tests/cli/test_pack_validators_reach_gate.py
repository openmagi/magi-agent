"""Task 3.3 — pack-discovered validator refs reach the existing gate.

``_merge_pack_validator_refs`` is the pure confirm/route helper: it appends
pack-discovered validator refs to the gate's ``required_validators`` (the left
side of ``cli/engine.py``'s ``missing_validators`` comprehension), order-stable
and dedup-on-merge. We do NOT touch the engine comparison.
"""
from __future__ import annotations

from magi_agent.cli.real_runner import _merge_pack_validator_refs


def test_pack_validator_refs_are_appended_for_gate() -> None:
    base = ("verifier:dev-coding:test-evidence",)
    pack_validator_refs = ("verifier:sourceOpened@1", "verifier:userQuote@1")
    merged = _merge_pack_validator_refs(base, pack_validator_refs)
    assert merged[0] == "verifier:dev-coding:test-evidence"
    assert "verifier:sourceOpened@1" in merged
    assert "verifier:userQuote@1" in merged


def test_pack_validator_refs_dedupe_against_base() -> None:
    base = ("verifier:sourceOpened@1",)
    merged = _merge_pack_validator_refs(base, ("verifier:sourceOpened@1",))
    assert merged.count("verifier:sourceOpened@1") == 1
