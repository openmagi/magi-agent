"""SmartApprove read-only classifier for the Magi permission gate (PR3).

Provides a manifest-first → cache → LLM classifier that, when wired into
``RulesPermissionGate`` via ``smart_approve=``, can recover a rule-miss ``ask``
as ``allow`` for read-only tools — without ever overriding an explicit ``deny``.

Default behaviour (``smart_approve=None``) is byte-identical to today.

Key invariants
--------------
- **Manifest-first**: known tools are classified from their ``ToolManifest``
  metadata (deterministic, no LLM needed for the vast majority of decisions).
- **Fail closed**: any exception in the LLM path or missing model_factory
  returns ``False`` → the gate falls through to the normal ``ask`` race → safe
  deny if no sink.
- **Per-session in-memory cache**: only tool-name-keyed (not argument-keyed);
  reproducible via the evidence log; no disk persistence in v1.
- **Evidence logging**: every classification (manifest / cache / llm /
  classifier_error) emits a record via the injected ``evidence_sink``.
- **No network calls in tests**: inject a ``model_factory`` that returns a fake.

Evidence type
-------------
All emitted evidence records carry ``type = SMART_APPROVE_EVIDENCE_TYPE``
(``"custom:SmartApproveClassification"``), which satisfies
``validate_evidence_type_name`` as a valid custom evidence type. Fields::

    {
        "type":    str,   # SMART_APPROVE_EVIDENCE_TYPE
        "tool":    str,
        "verdict": bool,
        "reason":  str,
        "source":  "manifest" | "cache" | "llm" | "classifier_error",
        "model":   str | None,
    }

LLM prompt
----------
Mirrors the goose read-only rubric (SELECT/read → read-only; write / INSERT /
UPDATE / DELETE / send → not; "if unsure → not read-only") but feeds
``name + description + input_schema`` so the model gets richer context.
The response MUST be strict JSON ``{"read_only": bool, "reason": str}``.

Model resolution
----------------
1. ``model_factory`` kwarg (injectable for tests — always use a fake).
2. ``MAGI_SMART_APPROVE_MODEL`` env var (overrides the main model string).
3. Main model from ``ProviderConfig`` (if available).
4. Fall closed if nothing resolves.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi_agent.cli.contracts import ControlRequest
    from magi_agent.tools.registry import ToolRegistry

__all__ = [
    "SMART_APPROVE_EVIDENCE_TYPE",
    "ReadOnlyClassifier",
]

# Registered custom evidence type — satisfies validate_evidence_type_name().
SMART_APPROVE_EVIDENCE_TYPE: str = "custom:SmartApproveClassification"

# Model env override — allows a faster/cheaper model for classification.
_ENV_MODEL_OVERRIDE = "MAGI_SMART_APPROVE_MODEL"

_READ_ONLY_PROMPT_TEMPLATE = """\
You are a tool-safety classifier. Decide whether the tool below is STRICTLY
READ-ONLY (performs no writes, mutations, side effects, network sends, or
process execution).

Rules:
- SELECT / read / list / describe → read-only
- write / INSERT / UPDATE / DELETE / send / execute / mutate → NOT read-only
- If unsure → NOT read-only (fail safe)

Tool information:
  name: {name}
  description: {description}
  input_schema: {input_schema}

Reply with ONLY a JSON object with no additional text:
{{"read_only": <bool>, "reason": "<one-sentence reason>"}}
"""


class ReadOnlyClassifier:
    """Manifest-first → cache → LLM read-only classifier.

    Parameters
    ----------
    registry:
        A ``ToolRegistry`` used for manifest-first decisions. May be ``None``
        (all tools fall through to the LLM step).
    model_factory:
        Zero-argument callable returning a LiteLlm-compatible model object
        (must expose ``generate_content_async``). When ``None``, the classifier
        attempts to build one from the provider config / env. Tests MUST inject
        a fake here to prevent real network calls.
    evidence_sink:
        Optional ``Callable[[dict], None]`` that receives every classification
        record. Never raises (errors are suppressed).
    provider_config:
        Optional ``ProviderConfig`` used to build the model when
        ``model_factory`` is ``None``.
    """

    def __init__(
        self,
        *,
        registry: "ToolRegistry | None" = None,
        model_factory: Callable[[], object] | None = None,
        evidence_sink: Callable[[dict], None] | None = None,
        provider_config: object = None,
    ) -> None:
        self._registry = registry
        self._model_factory = model_factory
        self._evidence_sink = evidence_sink
        self._provider_config = provider_config
        # Per-session in-memory cache: tool_name -> bool
        self._cache: dict[str, bool] = {}

    # ------------------------------------------------------------------
    # manifest_verdict — deterministic, no LLM
    # ------------------------------------------------------------------

    def manifest_verdict(self, tool_name: str) -> bool | None:
        """Return the read-only verdict from the manifest, or ``None`` if unknown.

        Returns
        -------
        ``True``
            Tool is known AND safe to classify as read-only:
            ``not dangerous`` AND ``not mutates_workspace``
            AND ``side_effect_class == "none"``
            AND ``parallel_safety in {"readonly", "concurrency_safe"}``.
        ``False``
            Tool is known AND at least one of the above conditions fails.
        ``None``
            Tool is not registered in the registry (unknown → LLM path).
        """
        if self._registry is None:
            return None
        manifest = self._registry.resolve(tool_name)
        if manifest is None:
            return None
        if (
            manifest.dangerous
            or manifest.mutates_workspace
            or manifest.side_effect_class != "none"
        ):
            return False
        return manifest.parallel_safety in ("readonly", "concurrency_safe")

    # ------------------------------------------------------------------
    # classify — manifest → cache → LLM, fail closed
    # ------------------------------------------------------------------

    async def classify(self, req: "ControlRequest") -> bool:
        """Classify a tool request as read-only.

        Priority:
        1. manifest_verdict (deterministic)
        2. per-session cache (name-keyed)
        3. LLM classify
        4. FAIL CLOSED → return False on any error

        Every path emits one evidence record.
        """
        tool_name = req.tool_name

        # 1. Manifest-first
        mv = self.manifest_verdict(tool_name)
        if mv is not None:
            self._emit(tool_name, verdict=mv, reason="manifest", source="manifest", model=None)
            return mv

        # 2. Cache
        if tool_name in self._cache:
            cached_verdict = self._cache[tool_name]
            self._emit(
                tool_name,
                verdict=cached_verdict,
                reason="cached from prior LLM classification",
                source="cache",
                model=None,
            )
            return cached_verdict

        # 3. LLM
        return await self._llm_classify(req)

    # ------------------------------------------------------------------
    # Internal: LLM classification
    # ------------------------------------------------------------------

    async def _llm_classify(self, req: "ControlRequest") -> bool:
        """Invoke the LLM classifier. Returns False on ANY failure (fail closed)."""
        tool_name = req.tool_name
        model_name: str | None = None
        try:
            model = self._resolve_model()
            if model is None:
                raise RuntimeError("no model available for SmartApprove classification")

            model_name = getattr(model, "model", None) or getattr(model, "_model", None)

            # Build the manifest description if available
            description = ""
            input_schema_str = "{}"
            if self._registry is not None:
                manifest = self._registry.resolve(tool_name)
                if manifest is not None:
                    description = manifest.description
                    input_schema_str = json.dumps(manifest.input_schema)

            prompt = _READ_ONLY_PROMPT_TEMPLATE.format(
                name=tool_name,
                description=description or "(unknown)",
                input_schema=input_schema_str,
            )

            response = await model.generate_content_async(prompt)  # type: ignore[attr-defined]
            raw_text = getattr(response, "text", None) or ""
            parsed = self._parse_llm_response(raw_text)
            if parsed is None:
                raise ValueError(f"LLM returned non-parseable response: {raw_text!r}")

            verdict = bool(parsed["read_only"])
            reason = str(parsed.get("reason", ""))
            # Cache the result
            self._cache[tool_name] = verdict
            self._emit(tool_name, verdict=verdict, reason=reason, source="llm", model=model_name)
            return verdict

        except Exception as exc:  # noqa: BLE001 — fail closed
            reason = f"{type(exc).__name__}: {exc}"
            self._emit(
                tool_name,
                verdict=False,
                reason=reason,
                source="classifier_error",
                model=model_name,
            )
            return False

    def _resolve_model(self) -> object | None:
        """Return a model object or None (fail closed — no exception raised)."""
        # 1. Injected factory (test seam)
        if self._model_factory is not None:
            try:
                return self._model_factory()
            except Exception:  # noqa: BLE001
                return None

        # 2. Build from provider config
        if self._provider_config is not None:
            try:
                return _build_litellm_for_config(self._provider_config)
            except Exception:  # noqa: BLE001
                return None

        # 3. Nothing available
        return None

    @staticmethod
    def _parse_llm_response(text: str) -> dict | None:
        """Parse the LLM JSON response. Returns None on failure."""
        text = text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.splitlines()
            # Remove first and last fence lines
            inner = lines[1:-1] if len(lines) >= 3 else lines
            text = "\n".join(inner).strip()
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(parsed, dict):
            return None
        if "read_only" not in parsed:
            return None
        if not isinstance(parsed["read_only"], bool):
            # Coerce JSON truthy/falsy strings defensively, but only strict booleans
            return None
        return parsed

    def _emit(
        self,
        tool: str,
        *,
        verdict: bool,
        reason: str,
        source: str,
        model: str | None,
    ) -> None:
        """Emit an evidence record to the injected sink (best-effort, never raises)."""
        if self._evidence_sink is None:
            return
        try:
            self._evidence_sink(
                {
                    "type": SMART_APPROVE_EVIDENCE_TYPE,
                    "tool": tool,
                    "verdict": verdict,
                    "reason": reason,
                    "source": source,
                    "model": model,
                }
            )
        except Exception:  # noqa: BLE001 — evidence sink errors never break the gate
            pass


# ---------------------------------------------------------------------------
# LiteLlm model builder (mirrors real_runner._build_litellm_model)
# ---------------------------------------------------------------------------

def _build_litellm_for_config(provider_config: object) -> object:
    """Build a LiteLlm model from a ``ProviderConfig``; raises on failure."""
    try:
        from google.adk.models.lite_llm import LiteLlm  # noqa: PLC0415
    except Exception as exc:
        raise RuntimeError("litellm dependency not available") from exc

    # Honour the fast-model override env var
    model_override = os.environ.get(_ENV_MODEL_OVERRIDE, "").strip()
    litellm_model = model_override or getattr(provider_config, "litellm_model", None)
    api_key = getattr(provider_config, "api_key", None)
    if not litellm_model:
        raise RuntimeError("cannot determine litellm model for SmartApprove")
    return LiteLlm(model=litellm_model, api_key=api_key)
