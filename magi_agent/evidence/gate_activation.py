"""F-11 — single activation helper for two-flag gate configs.

``evidence/final_output_gate.FinalOutputGateConfig`` and
``recipes/coding_evidence_gate.CodingEvidenceGateConfig`` each define
two boolean activation fields:

- ``enabled`` (master switch),
- ``local_evaluation_enabled`` (local-eval gate; production blocking
  is encoded separately as ``Literal[False]`` authority).

Pre-F-11 each gate inlined the same ``if not config.enabled or not
config.local_evaluation_enabled`` short-circuit at its evaluation
entry point. A reader couldn't tell whether one boolean implied the
other or whether a future gate had drifted; a new gate could ship
with only ``enabled`` checked. This module is the **single activation
predicate** both gates consult.

The helper is duck-typed: any config exposing ``.enabled`` (or
``enabledForChannel`` alias) and ``.local_evaluation_enabled`` is
acceptable. Both fields default to ``False`` if absent, so an
incomplete duck still resolves to ``False`` — fail-closed by
construction.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class _GateActivationConfig(Protocol):
    """Structural shape of a config the activation predicate accepts."""

    enabled: bool
    local_evaluation_enabled: bool


def gate_is_live(config: object) -> bool:
    """Return ``True`` iff *config* enables LOCAL evaluation.

    Equivalent to ``config.enabled and config.local_evaluation_enabled``,
    duck-typed (missing attrs read as ``False`` — fail-closed).
    """

    enabled = bool(getattr(config, "enabled", False))
    local = bool(getattr(config, "local_evaluation_enabled", False))
    return enabled and local


__all__ = ["gate_is_live"]
