"""First-party recipe packs — classification (H5 honesty pass).

Every pack in this directory uses ``Literal[False]`` attach-flag models. That
looks like "declared but inert" from the inside, but for most packs that is an
intentional **split architecture**, not a fake toggle:

* The pack is metadata-only — it carries instruction / validator / evidence
  refs that the recipe compiler aggregates into a ``RecipeSnapshot`` when the
  pack is selected by a task profile.
* The actual runtime *execution* lives in a separate module behind its own
  default-OFF env gate, NOT in the pack. The pack's ``Literal[False]`` attach
  flags are a contract guarantee that the metadata never attaches a live
  runner/tool/callback on its own.

Classification (verified against current main, 2026-06-19):

==================  =====================================  ==============================
Pack                Runtime execution lives in             Activation gate flag
==================  =====================================  ==============================
discovery           ``magi_agent.discovery`` (orchestrator) ``MAGI_DISCOVERY_ENABLED``
self_improvement    ``magi_agent`` learning subsystem       ``MAGI_LEARNING_*`` family
memory_recall       ``magi_agent.memory``                   ``MAGI_MEMORY_RECALL_ENABLED``
learning_usage      ``magi_agent`` learning subsystem       ``MAGI_LEARNING_ENABLED``
coding/ownership    — (no execution track planned)          — (intentional dormant)
==================  =====================================  ==============================

Each of the first four is an honest **opt-in** capability: enabling its env gate
flips the runtime execution on. The pack itself stays metadata-only by design.

``coding/ownership`` is the one genuine PR1 fixture-only scaffold: it documents
the required coding mechanic ids and their ownership boundary as a contract
fixture, with NO execution track planned. Its docstring labels it as such.

This module is intentionally empty of exports; the docstring is the contract.
``__all__`` stays empty so importing this package surfaces nothing
runtime-relevant.
"""
from __future__ import annotations

__all__ = ()
