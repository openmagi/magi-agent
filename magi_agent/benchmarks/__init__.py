"""Benchmark pieces consumed by the runtime (legal eval + legalbench).

Standalone benchmark harnesses (gaia, taubench, multibug, swebench,
coding_eval) live in top-level ``benchmarks/`` and are not shipped in
the wheel. Only ``legal_eval`` and ``legalbench`` stay here because the
``magi legal-eval`` CLI command and the first-party legal recipe import
them.
"""
from __future__ import annotations
