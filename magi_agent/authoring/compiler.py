from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from magi_agent.authoring.contracts import (
    DraftRecipePack,
    RecipeBuilderSession,
)


_DIGEST_PREFIX = "sha256:"
_MODEL_CONFIG = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")
_Blocker = Callable[[str, str, str | None, str | None], None]


class _CompilerModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for authoring compiler contracts")

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True)
        if update:
            for key, value in update.items():
                data[_copy_update_alias(type(self), key)] = value
        return type(self).model_validate(data)


class CompileRecipePackCatalog(_CompilerModel):
    connector_refs: tuple[str, ...] = Field(default=(), alias="connectorRefs")
    tool_refs: tuple[str, ...] = Field(default=(), alias="toolRefs")
    plugin_refs: tuple[str, ...] = Field(default=(), alias="pluginRefs")
    validator_refs: tuple[str, ...] = Field(default=(), alias="validatorRefs")
    harness_refs: tuple[str, ...] = Field(default=(), alias="harnessRefs")
    required_evidence_refs: tuple[str, ...] = Field(
        default=(), alias="requiredEvidenceRefs"
    )
    evidence_producer_refs: tuple[str, ...] = Field(
        default=(), alias="evidenceProducerRefs"
    )
    approval_authority_refs: tuple[str, ...] = Field(
        default=("authority:owner-human@1",), alias="approvalAuthorityRefs"
    )
    hard_invariant_refs: tuple[str, ...] = Field(default=(), alias="hardInvariantRefs")
    required_hard_invariant_refs: tuple[str, ...] = Field(
        default=("invariant.no-live-execution", "invariant.no-activation"),
        alias="requiredHardInvariantRefs",
    )

    @classmethod
    def default(cls) -> CompileRecipePackCatalog:
        return cls(
            connectorRefs=("connector.source.readonly",),
            toolRefs=(
                "BrowserLive",
                "CitationVerify",
                "FileWrite",
                "SourceOpen",
            ),
            pluginRefs=("plugin.source-review.readonly",),
            validatorRefs=("validator:sourceOpened@1", "validator:quoteExactMatch@1"),
            harnessRefs=("harness:authoring-static@1",),
            requiredEvidenceRefs=("openedSourceSnapshot", "quoteDigest"),
            evidenceProducerRefs=("evidence:source-opened@1", "evidence:quote-digest@1"),
            approvalAuthorityRefs=("authority:owner-human@1",),
            hardInvariantRefs=("invariant.no-live-execution", "invariant.no-activation"),
            requiredHardInvariantRefs=(
                "invariant.no-live-execution",
                "invariant.no-activation",
            ),
        )

    @field_validator(
        "connector_refs",
        "tool_refs",
        "plugin_refs",
        "validator_refs",
        "harness_refs",
        "required_evidence_refs",
        "evidence_producer_refs",
        "approval_authority_refs",
        "hard_invariant_refs",
        "required_hard_invariant_refs",
    )
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError("catalog refs must be non-empty strings")
        return value

    @model_validator(mode="after")
    def _validate_required_hard_invariants(self) -> CompileRecipePackCatalog:
        missing = set(self.required_hard_invariant_refs).difference(self.hard_invariant_refs)
        if missing:
            raise ValueError("requiredHardInvariantRefs must be declared in hardInvariantRefs")
        return self


class CompileRecipePackDiagnostic(_CompilerModel):
    code: str
    message: str
    severity: Literal["error", "warning"] = "error"
    path: str | None = None
    ref: str | None = None


class HardInvariantResult(_CompilerModel):
    invariant_id: str = Field(alias="invariantId")
    ok: bool
    mode: str
    message: str


class CompileRecipePackResult(_CompilerModel):
    ok: bool
    compiled_snapshot_digest: str | None = Field(
        default=None, alias="compiledSnapshotDigest"
    )
    effective_policy_snapshot_digest: str | None = Field(
        default=None, alias="effectivePolicySnapshotDigest"
    )
    diagnostics: tuple[CompileRecipePackDiagnostic, ...] = ()
    warnings: tuple[CompileRecipePackDiagnostic, ...] = ()
    blocked_reasons: tuple[str, ...] = Field(default=(), alias="blockedReasons")
    hard_invariant_results: tuple[HardInvariantResult, ...] = Field(
        default=(), alias="hardInvariantResults"
    )


def compile_recipe_pack(
    session: RecipeBuilderSession | Mapping[str, object],
    *,
    catalog: CompileRecipePackCatalog | None = None,
) -> CompileRecipePackResult:
    reference_catalog = catalog if catalog is not None else resolve_live_catalog()
    diagnostics: list[CompileRecipePackDiagnostic] = []
    warnings: list[CompileRecipePackDiagnostic] = []
    blocked_reasons: list[str] = []

    def block(
        code: str,
        message: str,
        path: str | None = None,
        ref: str | None = None,
    ) -> None:
        if ref is not None:
            reason = f"{code}:{ref}"
        else:
            reason = code
        blocked_reasons.append(reason)
        diagnostics.append(
            CompileRecipePackDiagnostic(
                code=code,
                message=message,
                path=path,
                ref=ref,
            )
        )

    try:
        builder_session = (
            session
            if isinstance(session, RecipeBuilderSession)
            else RecipeBuilderSession.model_validate(session)
        )
    except ValidationError as exc:
        code = _scope_error_code(exc)
        return CompileRecipePackResult(
            ok=False,
            diagnostics=(
                CompileRecipePackDiagnostic(
                    code=code,
                    message=str(exc),
                    path="session",
                ),
            ),
            blockedReasons=(code,),
        )

    if builder_session.mode != "recipe_builder":
        block(
            "invalid_recipe_builder_scope",
            "CompileRecipePack requires temporary recipe_builder mode scope.",
            path="mode",
        )
    if builder_session.draft is None:
        block(
            "missing_recipe_pack_draft",
            "CompileRecipePack requires a bot-scoped draft on the builder session.",
            path="draft",
        )
        return _blocked_result(diagnostics, warnings, blocked_reasons)

    draft = builder_session.draft
    pack = draft.pack
    _validate_refs(
        pack.tool_policy.allowed_connector_refs,
        known=reference_catalog.connector_refs,
        code="unknown_connector_ref",
        path="draft.pack.toolPolicy.allowedConnectorRefs",
        block=block,
    )
    _validate_refs(
        pack.tool_policy.allowed_tool_refs + pack.tool_policy.denied_tool_refs,
        known=reference_catalog.tool_refs,
        code="unknown_tool_ref",
        path="draft.pack.toolPolicy",
        block=block,
    )
    _validate_refs(
        pack.tool_policy.allowed_plugin_refs,
        known=reference_catalog.plugin_refs,
        code="unknown_plugin_ref",
        path="draft.pack.toolPolicy.allowedPluginRefs",
        block=block,
    )
    _validate_refs(
        pack.validator_policy.validator_refs,
        known=reference_catalog.validator_refs,
        code="unknown_validator_ref",
        path="draft.pack.validatorPolicy.validatorRefs",
        block=block,
    )
    _validate_refs(
        pack.harness_policy.harness_refs,
        known=reference_catalog.harness_refs,
        code="unknown_harness_ref",
        path="draft.pack.harnessPolicy.harnessRefs",
        block=block,
    )
    _validate_refs(
        pack.evidence_policy.required_evidence_refs,
        known=reference_catalog.required_evidence_refs,
        code="unknown_required_evidence_ref",
        path="draft.pack.evidencePolicy.requiredEvidenceRefs",
        block=block,
    )
    _validate_refs(
        pack.evidence_policy.evidence_producer_refs,
        known=reference_catalog.evidence_producer_refs,
        code="unknown_evidence_producer_ref",
        path="draft.pack.evidencePolicy.evidenceProducerRefs",
        block=block,
    )
    _validate_refs(
        pack.approval_policy.authority_refs,
        known=reference_catalog.approval_authority_refs,
        code="unknown_approval_authority_ref",
        path="draft.pack.approvalPolicy.authorityRefs",
        block=block,
    )

    if (
        pack.projection_policy.mode == "raw_governed"
        or pack.projection_policy.raw_governed_projection_enabled
    ):
        block(
            "raw_governed_projection_disabled",
            "Raw governed projection is disabled for authoring compilation.",
            path="draft.pack.projectionPolicy",
        )

    if pack.approval_policy.requires_human_review and not pack.approval_policy.authority_refs:
        block(
            "approval_authority_missing",
            "Human review approval policy requires at least one authority ref.",
            path="draft.pack.approvalPolicy.authorityRefs",
        )

    if pack.repair_policy.max_repair_attempts > 0 and not pack.repair_policy.terminal_states:
        block(
            "repair_terminal_state_missing",
            "Repair policy with attempts must declare terminal states.",
            path="draft.pack.repairPolicy.terminalStates",
        )

    for field_name, value in (
        ("maxToolCalls", pack.budget_policy.max_tool_calls),
        ("maxValidatorCalls", pack.budget_policy.max_validator_calls),
        ("maxRepairAttempts", pack.budget_policy.max_repair_attempts),
    ):
        if value <= 0:
            block(
                "budget_cap_invalid",
                f"{field_name} must be greater than zero.",
                path=f"draft.pack.budgetPolicy.{field_name}",
                ref=field_name,
            )

    hard_invariant_results = _validate_hard_invariants(pack, reference_catalog, block)

    if blocked_reasons:
        return _blocked_result(
            diagnostics,
            warnings,
            blocked_reasons,
            hard_invariant_results=hard_invariant_results,
        )

    effective_policy = _effective_policy_snapshot(builder_session, pack)
    compiled_snapshot = {
        "draft": draft.model_dump(by_alias=True, mode="json", exclude_none=True),
        "effectivePolicy": effective_policy,
        "scope": {
            "botId": builder_session.bot_id,
            "ownerId": builder_session.owner_id,
            "sessionId": builder_session.session_id,
            "mode": builder_session.mode,
        },
    }

    return CompileRecipePackResult(
        ok=True,
        compiledSnapshotDigest=_digest(compiled_snapshot),
        effectivePolicySnapshotDigest=_digest(effective_policy),
        diagnostics=tuple(diagnostics),
        warnings=tuple(warnings),
        blockedReasons=(),
        hardInvariantResults=hard_invariant_results,
    )


def _dedup(refs: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            out.append(ref)
    return tuple(out)


def resolve_live_catalog(
    *,
    env: Mapping[str, str] | None = None,
) -> CompileRecipePackCatalog:
    """Build the live catalog from loaded pack manifests (D4).

    Replaces the hardcoded ``CompileRecipePackCatalog.default()`` on the live
    ``None``-catalog path: discovers packs (bundled first-party + user dirs),
    reads their static ``provides`` refs, and folds them into a flat catalog —
    no first-party-only tier (§1 no privilege). A user pack's refs land in
    exactly the same fields as first-party's.

    The legacy ``.default()`` reference floor is PRESERVED (unioned in) so that
    existing recipe-ref validation (and the hosted hard-invariant floor the model
    validator at ``CompileRecipePackCatalog._validate_required_hard_invariants``
    requires) keeps passing — this flip adds pack-discovered refs without dropping
    any reference the live runtime already validated against. Discovery failures
    fail open to the static default (the runtime stays usable with no packs).
    """
    floor = CompileRecipePackCatalog.default()
    try:
        from magi_agent.packs.catalog_build import build_catalog  # noqa: PLC0415
        from magi_agent.packs.discovery import (  # noqa: PLC0415
            default_search_bases,
            discover_pack_files,
            load_packs_config,
            resolve_enabled_packs,
        )
        from magi_agent.packs.loader import RecordingSink, load_packs  # noqa: PLC0415

        discovered = discover_pack_files(default_search_bases())
        enabled = resolve_enabled_packs(discovered, load_packs_config())
        sink = RecordingSink()
        result = load_packs(enabled, sink)
        pack_catalog = build_catalog(result.primitives)
    except Exception:
        return floor

    return CompileRecipePackCatalog(
        connectorRefs=_dedup(floor.connector_refs + pack_catalog.connector_refs),
        toolRefs=_dedup(floor.tool_refs + pack_catalog.tool_refs),
        pluginRefs=_dedup(floor.plugin_refs + pack_catalog.plugin_refs),
        validatorRefs=_dedup(floor.validator_refs + pack_catalog.validator_refs),
        harnessRefs=_dedup(floor.harness_refs + pack_catalog.harness_refs),
        requiredEvidenceRefs=floor.required_evidence_refs,
        evidenceProducerRefs=_dedup(
            floor.evidence_producer_refs + pack_catalog.evidence_producer_refs
        ),
        approvalAuthorityRefs=floor.approval_authority_refs,
        # Preserve the hosted hard-invariant floor (out of scope for pack refs).
        hardInvariantRefs=floor.hard_invariant_refs,
        requiredHardInvariantRefs=floor.required_hard_invariant_refs,
    )


def _scope_error_code(exc: ValidationError) -> str:
    for error in exc.errors(include_url=False):
        loc = tuple(str(item) for item in error.get("loc", ()))
        if loc and loc[0] in {"mode", "botId", "ownerId", "sessionId", "draft"}:
            return "invalid_recipe_builder_scope"
    return "schema_validation_failed"


def _copy_update_alias(model_type: type[BaseModel], key: str) -> str:
    field = model_type.model_fields.get(key)
    if field is not None and field.alias is not None:
        return field.alias
    return key


def _blocked_result(
    diagnostics: list[CompileRecipePackDiagnostic],
    warnings: list[CompileRecipePackDiagnostic],
    blocked_reasons: list[str],
    *,
    hard_invariant_results: tuple[HardInvariantResult, ...] = (),
) -> CompileRecipePackResult:
    return CompileRecipePackResult(
        ok=False,
        diagnostics=tuple(diagnostics),
        warnings=tuple(warnings),
        blockedReasons=tuple(blocked_reasons),
        hardInvariantResults=hard_invariant_results,
    )


def _validate_refs(
    refs: tuple[str, ...],
    *,
    known: tuple[str, ...],
    code: str,
    path: str,
    block: _Blocker,
) -> None:
    known_refs = set(known)
    for ref in refs:
        if ref not in known_refs:
            block(code, f"Unknown reference: {ref}", path=path, ref=ref)


def _validate_hard_invariants(
    pack: DraftRecipePack,
    catalog: CompileRecipePackCatalog,
    block: _Blocker,
) -> tuple[HardInvariantResult, ...]:
    results: list[HardInvariantResult] = []
    if not pack.hard_invariants:
        block(
            "hard_invariant_missing",
            "CompileRecipePack requires at least one enforced hard invariant.",
            path="draft.pack.hardInvariants",
        )
        return ()

    hard_invariant_ids = {invariant.invariant_id for invariant in pack.hard_invariants}
    for required_ref in catalog.required_hard_invariant_refs:
        if required_ref not in hard_invariant_ids:
            block(
                "required_hard_invariant_missing",
                "Required hard invariant is missing from the draft.",
                path="draft.pack.hardInvariants",
                ref=required_ref,
            )

    for invariant in pack.hard_invariants:
        known_invariant = invariant.invariant_id in catalog.hard_invariant_refs
        if not known_invariant:
            block(
                "unknown_hard_invariant_ref",
                "Hard invariant is not declared in the compiler catalog.",
                path="draft.pack.hardInvariants",
                ref=invariant.invariant_id,
            )
        enforced = invariant.mode == "enforced"
        if not enforced:
            block(
                "hard_invariant_not_enforced",
                "Hard invariants must be enforced, not disabled or log-only.",
                path="draft.pack.hardInvariants",
                ref=invariant.invariant_id,
            )
        ok = known_invariant and enforced
        results.append(
            HardInvariantResult(
                invariantId=invariant.invariant_id,
                ok=ok,
                mode=invariant.mode,
                message=(
                    "Hard invariant is enforced."
                    if ok
                    else "Hard invariant is not an enforced catalog invariant."
                ),
            )
        )
    return tuple(results)


def _effective_policy_snapshot(
    session: RecipeBuilderSession,
    pack: DraftRecipePack,
) -> dict[str, object]:
    return {
        "approvalPolicy": pack.approval_policy.model_dump(
            by_alias=True, mode="json", exclude_none=True
        ),
        "budgetPolicy": pack.budget_policy.model_dump(
            by_alias=True, mode="json", exclude_none=True
        ),
        "evidencePolicy": pack.evidence_policy.model_dump(
            by_alias=True, mode="json", exclude_none=True
        ),
        "hardInvariants": [
            invariant.model_dump(by_alias=True, mode="json", exclude_none=True)
            for invariant in pack.hard_invariants
        ],
        "harnessPolicy": pack.harness_policy.model_dump(
            by_alias=True, mode="json", exclude_none=True
        ),
        "projectionPolicy": pack.projection_policy.model_dump(
            by_alias=True, mode="json", exclude_none=True
        ),
        "repairPolicy": pack.repair_policy.model_dump(
            by_alias=True, mode="json", exclude_none=True
        ),
        "scope": {
            "botId": session.bot_id,
            "ownerId": session.owner_id,
            "sessionId": session.session_id,
            "mode": session.mode,
        },
        "toolPolicy": pack.tool_policy.model_dump(
            by_alias=True, mode="json", exclude_none=True
        ),
        "validatorPolicy": pack.validator_policy.model_dump(
            by_alias=True, mode="json", exclude_none=True
        ),
    }


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{_DIGEST_PREFIX}{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"
