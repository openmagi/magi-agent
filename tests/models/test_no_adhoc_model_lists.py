"""E-1 invariant lock: only ``magi_agent/models/`` may declare model lists.

This meta-test AST-walks ``cli/``/``runtime/`` and asserts that NO module
outside ``magi_agent.models`` defines a hardcoded provider→model dict literal
or a literal ``("anthropic", "haiku")``-style hardcoded tuple table.

If a future refactor reintroduces a hand-maintained model table, this test
catches it before it can drift.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "magi_agent"

# The provider-name surface the meta-test scans dict literals for.
PROVIDER_NAMES = {
    "anthropic",
    "openai",
    "gemini",
    "google",
    "fireworks",
    "openrouter",
}

# Modules allowed to define such tables (the single source of truth + tests).
ALLOWED_RELATIVE = {
    "models/catalog.py",
    "models/types.py",
    "models/export_ts.py",
}


def _iter_py_files(*subdirs: str) -> list[Path]:
    out: list[Path] = []
    for sub in subdirs:
        out.extend((ROOT / sub).rglob("*.py"))
    return out


def _is_excluded(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    if rel in ALLOWED_RELATIVE:
        return True
    # tests/fixtures live inside the package tree for some sub-packages.
    if "/tests/" in rel or rel.startswith("tests/"):
        return True
    return False


_MODEL_ID_RE = __import__("re").compile(
    # Spot a model-id literal: claude-*/gpt-*/gemini-*/kimi-*/o1/o3/o4
    # families. Avoid false-positives by NOT matching short generic words.
    r"^(claude-|gpt-|gemini-|kimi-|minimax-|haiku|accounts/fireworks)",
    __import__("re").IGNORECASE,
)


def _looks_like_model_id(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return bool(_MODEL_ID_RE.match(value))


def _dict_value_is_model_id(node: ast.expr) -> bool:
    """Check whether an ``ast.Dict`` value literal looks like a model id.

    Recurses into tuple/list literals (``{"openai": ("gpt-5.5",)}``) and
    string constants only. Anything else is conservatively ignored.
    """
    if isinstance(node, ast.Constant):
        return _looks_like_model_id(node.value)
    if isinstance(node, (ast.Tuple, ast.List)):
        return any(_dict_value_is_model_id(el) for el in node.elts)
    return False


def _dict_literal_has_provider_keys(node: ast.Dict) -> bool:
    """Heuristic: is this a ``{"anthropic": "<model-id>", ...}`` table?

    Both conditions must hold:
    1. At least 2 string keys match :data:`PROVIDER_NAMES` (so a dict whose
       provider mention is incidental — e.g. only one key — slips through).
    2. At least one value LOOKS like a model id (``claude-*``, ``gpt-*``,
       ``gemini-*``, ``kimi-*``, ``minimax-*``, ``haiku``). This filters out
       legit provider-keyed dicts whose values are env-var names, prompt
       snippets, or reasoning-effort substitutions.
    """
    keys: list[str] = []
    for k in node.keys:
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            keys.append(k.value.lower())
    provider_hits = sum(1 for k in keys if k in PROVIDER_NAMES)
    if provider_hits < 2:
        return False
    return any(_dict_value_is_model_id(v) for v in node.values if v is not None)


class _DictLiteralVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.hits: list[ast.Dict] = []

    def visit_Dict(self, node: ast.Dict) -> None:  # noqa: N802 — ast API
        if _dict_literal_has_provider_keys(node):
            self.hits.append(node)
        self.generic_visit(node)


def test_no_provider_keyed_dict_literals_in_cli_or_runtime() -> None:
    """Forbid ``{"anthropic": ..., "openai": ...}``-shape literals.

    These were the legacy ``_DEFAULT_MODEL`` / ``_LITELLM_PREFIX`` /
    ``_materializer_model`` / phase-cost / etc. tables. After E-1 the catalog
    is the single source; any new such literal in ``cli/``/``runtime/`` is a
    regression toward the drift state.
    """
    offenders: list[str] = []
    for path in _iter_py_files("cli", "runtime"):
        if _is_excluded(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        visitor = _DictLiteralVisitor()
        visitor.visit(tree)
        for hit in visitor.hits:
            offenders.append(f"{path.relative_to(ROOT)}:{hit.lineno}")
    assert offenders == [], (
        "Provider-keyed dict literal found outside magi_agent/models/: "
        + ", ".join(offenders)
        + "\nUse magi_agent.models.catalog.ModelCatalog.builtin() instead."
    )


def test_materializer_model_no_longer_carries_hardcoded_tuples() -> None:
    """The legacy ``_materializer_model`` returned literal ``("openai","gpt-5.5")``-style
    tuples per provider. After E-1 it must query the catalog.

    We grep for the function body and assert it does NOT contain four
    consecutive ``return "<provider>", "<model>"``-shape literals.
    """
    real_runner = ROOT / "cli" / "real_runner.py"
    text = real_runner.read_text(encoding="utf-8")
    # Count concrete tuple-literal returns inside _materializer_model. The
    # legacy body had four (anthropic/openai/fireworks/google). After E-1 the
    # function should be a single catalog call, so we expect zero in that
    # specific shape ``return "anthropic", "haiku"``.
    legacy_pattern_hits = sum(
        text.count(snippet)
        for snippet in (
            'return "anthropic", "haiku"',
            'return "openai", "gpt-5.5"',
            'return "fireworks", "kimi-k2p6"',
            'return "google", "gemini-3.5-flash"',
        )
    )
    assert legacy_pattern_hits == 0, (
        "_materializer_model still contains legacy hardcoded provider→model "
        "returns; route the lookup through ModelCatalog.cheap_model_for instead."
    )


def test_with_defaults_no_longer_carries_hand_written_record_block() -> None:
    """``ModelTierRegistry.with_defaults`` must build from the catalog.

    The legacy implementation hand-wrote 9 ``_ModelTierRecord(...)`` calls.
    After E-1 it must delegate to ``from_catalog(ModelCatalog.builtin())`` so
    every record is sourced from ``builtin_catalog.json``.
    """
    model_tiers = ROOT / "runtime" / "model_tiers.py"
    text = model_tiers.read_text(encoding="utf-8")
    # The hand-written block had at least 6 distinct ``_ModelTierRecord(``
    # invocations inside ``with_defaults``. After E-1 we expect either zero
    # ``_ModelTierRecord(`` literal calls in ``with_defaults``'s lexical scope
    # OR the body to be exactly one ``return cls.from_catalog(...)``-shape.
    tree = ast.parse(text)
    found_record_calls_inside_with_defaults = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "with_defaults":
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == "_ModelTierRecord"
                ):
                    found_record_calls_inside_with_defaults += 1
    assert found_record_calls_inside_with_defaults == 0, (
        "ModelTierRegistry.with_defaults still constructs hand-written "
        "_ModelTierRecord literals; it must build from ModelCatalog.builtin()."
    )


if __name__ == "__main__":  # pragma: no cover — convenience runner
    pytest.main([__file__, "-v"])
