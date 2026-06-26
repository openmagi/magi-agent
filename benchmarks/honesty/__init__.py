"""Honesty bench — measure the divergence between what an agent *claims* it did
and what the evidence ledger *actually* recorded.

Two products share this package:

* ``scorer`` — pure claim-vs-evidence divergence scoring (Tier-A structured
  claims only; no LLM). Reusable by both the retrospective divergence metric
  and the adversarial 3-layer benchmark.
* ``loaders`` — parse the two on-disk JSONL shapes (session transcript +
  durable evidence ledger) into the ``(session_id, turn_id)``-keyed structures
  the scorer consumes.

Honesty guardrails are baked into the scorer, not bolted on: a claim with no
producer-emitted evidence is reported as ``absent`` (weak — could be a producer
gap), strictly separate from a claim a record actively *contradicts* (strong —
the receipt says it failed). The headline number is the contradicted subset.
"""
