from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from openmagi_core_agent.authoring.compiler import (
    CompileRecipePackCatalog,
    CompileRecipePackResult,
    compile_recipe_pack,
)
from openmagi_core_agent.authoring.contracts import (
    _AuthoringModel,
    _authoring_tool_scope_ids,
    _reject_false,
    _reject_true,
    _require_non_empty,
    AuthoringToolScope,
    AuthoringToolSession,
    BuilderGapReport,
    DraftHarnessPolicy as DraftHarnessPolicyData,
    EvalFixtureSet,
    GeneratedPluginProposal,
    RecipeBuilderSession,
    RecipePackDraft,
)
from openmagi_core_agent.authoring.dry_run import (
    DryRunRecipePackCatalog,
    DryRunRecipePackConfig,
    DryRunRecipePackRequest,
    DryRunRecipePackResult,
    dry_run_recipe_pack,
)

_MODEL_CONFIG = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")
_DIGEST_PREFIX = "sha256:"
_PRIVATE_PATH_PREFIXES = ("/", "~", "file:", "s3:", "gs:")
_UNSAFE_PUBLIC_REF_RE = re.compile(
    r"(?i)(bearer\s+[a-z0-9._-]{8,}|"
    r"(?:api[_-]?key|apikey|token|secret|password|credential)\s*[:=?&]\s*[^\s,;]+|"
    r"raw\s*(?:model\s*)?(?:prompt|output)|"
    r"hidden\s+instructions?|hidden\s+transcript|chain\s+of\s+thought|"
    r"tool\s+result\s+payload)"
)


def _copy_update_alias(model_type: type[BaseModel], key: str) -> str:
    field = model_type.model_fields.get(key)
    if field is not None and field.alias is not None:
        return field.alias
    return key


def _coerce_scope(value: object) -> AuthoringToolSession:
    if isinstance(value, RecipeBuilderSession | AuthoringToolScope):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("scope must be a RecipeBuilderSession or AuthoringToolScope")
    try:
        return RecipeBuilderSession.model_validate(value)
    except ValidationError:
        return AuthoringToolScope.model_validate(value)


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{_DIGEST_PREFIX}{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _scope_digest(scope: AuthoringToolSession) -> str:
    bot_id, owner_id, session_id = _authoring_tool_scope_ids(scope)
    return _digest(
        {
            "botId": bot_id,
            "ownerId": owner_id,
            "sessionId": session_id,
            "mode": "recipe_builder",
        }
    )


def _scope_matches_draft(scope: AuthoringToolSession, draft: RecipePackDraft) -> None:
    bot_id, owner_id, session_id = _authoring_tool_scope_ids(scope)
    if draft.bot_id != bot_id:
        raise ValueError("scope botId must match draft botId")
    if draft.owner_id != owner_id:
        raise ValueError("scope ownerId must match draft ownerId")
    if draft.authoring_session_id != session_id:
        raise ValueError("scope sessionId must match draft authoringSessionId")


def _require_non_empty_strings(values: tuple[str, ...], field_label: str) -> None:
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"{field_label} must contain non-empty strings")


def _require_public_ref(value: str, field_label: str) -> str:
    _require_non_empty(value, field_label)
    lowered = value.lower()
    if (
        lowered.startswith(_PRIVATE_PATH_PREFIXES)
        or "\\" in value
        or (len(value) > 2 and value[1] == ":" and value[2] in {"/", "\\"})
    ):
        raise ValueError(f"{field_label} cannot expose private paths")
    if _UNSAFE_PUBLIC_REF_RE.search(value):
        raise ValueError(f"{field_label} cannot expose credentials or raw model data")
    return value


def _require_digest(value: str, field_label: str) -> str:
    suffix = value.removeprefix(_DIGEST_PREFIX)
    if not value.startswith(_DIGEST_PREFIX) or len(suffix) != 64:
        raise ValueError(f"{field_label} must be a sha256 digest")
    if any(char not in "0123456789abcdef" for char in suffix):
        raise ValueError(f"{field_label} must be a sha256 digest")
    return value


def _require_builder_session_with_draft(
    scope: AuthoringToolSession,
    tool_name: str,
) -> RecipeBuilderSession:
    if not isinstance(scope, RecipeBuilderSession) or scope.draft is None:
        raise ValueError(f"{tool_name} requires RecipeBuilderSession scope with draft")
    return scope


def _validate_plugin_proposals(
    proposals: tuple[GeneratedPluginProposal, ...],
) -> tuple[GeneratedPluginProposal, ...]:
    for proposal in proposals:
        _reject_true(proposal.executable, "executable")
        if proposal.runtime_entrypoint is not None:
            raise ValueError("runtimeEntrypoint must be absent for generated plugin proposals")
        if not proposal.review_required:
            raise ValueError("reviewRequired cannot be false for generated plugin proposals")
    return proposals


class _AuthoringToolModel(_AuthoringModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for authoring tool contracts")

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True)
        if update:
            for key, value in update.items():
                data[_copy_update_alias(type(self), key)] = value
        return type(self).model_validate(data)


class _BaseAuthoringContract(_AuthoringToolModel):
    scope: AuthoringToolSession
    scope_digest: str = Field(alias="scopeDigest")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    non_production: Literal[True] = Field(default=True, alias="nonProduction")

    @model_validator(mode="before")
    @classmethod
    def _coerce_scope(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        maybe_scope = value.get("scope")
        if maybe_scope is not None:
            value = dict(value)
            value["scope"] = _coerce_scope(maybe_scope)
            if "scopeDigest" not in value:
                value["scopeDigest"] = _scope_digest(value["scope"])
        return value

    @model_validator(mode="after")
    def _require_defaults(self) -> Self:
        _reject_false(self.local_only, "localOnly")
        _reject_false(self.non_production, "nonProduction")
        if self.scope_digest != _scope_digest(self.scope):
            raise ValueError("scopeDigest must match scope")
        return self

    @field_validator("scope_digest")
    @classmethod
    def _require_digest(cls, value: str) -> str:
        _suffix = value.removeprefix(_DIGEST_PREFIX)
        if not value.startswith(_DIGEST_PREFIX) or len(_suffix) != 64:
            raise ValueError("scopeDigest must be a sha256 digest")
        if any(char not in "0123456789abcdef" for char in _suffix):
            raise ValueError("scopeDigest must be a sha256 digest")
        return value


class MagiDocDigest(_AuthoringModel):
    doc_id: str = Field(alias="docId")
    digest: str
    title: str
    summary: str

    @field_validator("doc_id", "title", "summary")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        return _require_public_ref(value, "magi docs")

    @field_validator("digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_digest(value, "magi docs digest")


class ReadMagiDocs(_BaseAuthoringContract):
    query: str | None = None
    docs: tuple[MagiDocDigest, ...] = Field(default_factory=tuple)

    @field_validator("query")
    @classmethod
    def _reject_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_public_ref(value, "query")


class _CatalogInspection(_BaseAuthoringContract):
    references: tuple[str, ...]
    catalog_digest: str = Field(alias="catalogDigest")

    @field_validator("references")
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _require_non_empty_strings(value, "registry references")
        for ref in value:
            _require_public_ref(ref, "registry references")
        return value

    @model_validator(mode="before")
    @classmethod
    def _default_catalog_digest(cls, value: object) -> object:
        if isinstance(value, Mapping):
            references = value.get("references")
            if references is None:
                references = cls.model_fields["references"].default
            catalog_digest = _digest({"references": tuple(references)})
            provided_digest = value.get("catalogDigest")
            if provided_digest is not None and provided_digest != catalog_digest:
                raise ValueError("catalogDigest must match catalog references")
            if "catalogDigest" not in value:
                value = dict(value)
                value["catalogDigest"] = catalog_digest
        return value


class InspectRecipeRegistry(_CatalogInspection):
    references: tuple[str, ...] = (
        "recipe.example-review",
        "recipe.example-reconciliation",
    )


class InspectToolCatalog(_CatalogInspection):
    references: tuple[str, ...] = (
        "SourceOpen",
        "CitationVerify",
        "BrowserLive",
        "FileWrite",
    )


class InspectPluginCatalog(_CatalogInspection):
    references: tuple[str, ...] = (
        "plugin.example-review.readonly",
        "plugin.example-reconciliation.readonly",
    )


class InspectConnectorAvailability(_CatalogInspection):
    references: tuple[str, ...] = (
        "connector.example-source.readonly",
        "connector.example-ledger.readonly",
    )


class InspectValidatorRegistry(_CatalogInspection):
    references: tuple[str, ...] = (
        "validator:exampleEvidencePresent@1",
        "validator:exampleRecordMatches@1",
    )


class InspectHarnessRegistry(_CatalogInspection):
    references: tuple[str, ...] = ("harness:authoring-static@1",)


class DraftHarnessPolicy(_BaseAuthoringContract):
    draft_id: str | None = Field(default=None, alias="draftId")
    harness_policy: DraftHarnessPolicyData = Field(
        default_factory=DraftHarnessPolicyData,
        alias="harnessPolicy",
    )
    activation_eligibility: Literal[False] = Field(
        default=False,
        alias="activationEligibility",
    )

    @field_validator("draft_id")
    @classmethod
    def _validate_draft_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_public_ref(value, "draftId")

    @model_validator(mode="after")
    def _require_default_off(self) -> Self:
        _reject_true(self.activation_eligibility, "activationEligibility")
        return self


class DraftRecipePack(_BaseAuthoringContract):
    draft: RecipePackDraft
    draft_only: Literal[True] = Field(default=True, alias="draftOnly")
    activation_eligibility: Literal[False] = Field(
        default=False,
        alias="activationEligibility",
    )
    activation_enabled: Literal[False] = Field(default=False, alias="activationEnabled")
    generated_plugin_proposals: tuple[GeneratedPluginProposal, ...] = Field(
        default=(),
        alias="generatedPluginProposals",
    )

    @model_validator(mode="before")
    @classmethod
    def _sync_generated_plugin_proposals(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        if "generatedPluginProposals" in value:
            return value
        draft = value.get("draft")
        if isinstance(draft, Mapping) and draft.get("generatedPluginProposals") is not None:
            value = dict(value)
            value["generatedPluginProposals"] = draft.get("generatedPluginProposals")
        return value

    @model_validator(mode="after")
    def _require_draft_only(self) -> Self:
        _scope_matches_draft(self.scope, self.draft)
        _reject_false(self.draft_only, "draftOnly")
        _reject_true(self.activation_eligibility, "activationEligibility")
        _reject_true(self.activation_enabled, "activationEnabled")
        if tuple(self.generated_plugin_proposals) != tuple(
            self.draft.generated_plugin_proposals
        ):
            raise ValueError("generatedPluginProposals must match draft proposals")
        _validate_plugin_proposals(self.generated_plugin_proposals)
        return self


class DraftEvalFixtures(_BaseAuthoringContract):
    draft_id: str = Field(alias="draftId")
    fixtures: tuple[EvalFixtureSet, ...] = Field(default_factory=tuple)

    @field_validator("draft_id")
    @classmethod
    def _validate_draft_id(cls, value: str) -> str:
        return _require_non_empty(value, "draftId")

    @model_validator(mode="after")
    def _validate_fixture_scope(self) -> Self:
        for fixture in self.fixtures:
            if fixture.draft_id != self.draft_id:
                raise ValueError("fixture draftId must match request draftId")
            _reject_false(fixture.local_only, "localOnly")
            _reject_false(fixture.non_production, "nonProduction")
        return self


class GenerateGapReport(_BaseAuthoringContract):
    draft_id: str = Field(alias="draftId")
    report: BuilderGapReport | None = None
    activation_eligibility: Literal[False] = Field(
        default=False,
        alias="activationEligibility",
    )

    @field_validator("draft_id")
    @classmethod
    def _validate_draft_id(cls, value: str) -> str:
        return _require_public_ref(value, "draftId")

    @model_validator(mode="after")
    def _validate_report_scope(self) -> Self:
        _reject_true(self.activation_eligibility, "activationEligibility")
        if self.report is None:
            return self
        _, _, session_id = _authoring_tool_scope_ids(self.scope)
        if self.report.session_id != session_id:
            raise ValueError("gap report sessionId must match scope sessionId")
        if self.report.draft_id != self.draft_id:
            raise ValueError("gap report draftId must match request draftId")
        _reject_false(self.report.local_only, "localOnly")
        _reject_false(self.report.non_production, "nonProduction")
        return self


class SaveRecipePackDraft(_BaseAuthoringContract):
    draft: RecipePackDraft
    saved_scope: Literal["current_bot_draft_store"] = Field(
        default="current_bot_draft_store",
        alias="savedScope",
    )
    activation_eligibility: Literal[False] = Field(
        default=False,
        alias="activationEligibility",
    )
    activation_enabled: Literal[False] = Field(default=False, alias="activationEnabled")
    draft_id: str = Field(default="", alias="draftId")

    @field_validator("draft_id")
    @classmethod
    def _require_draft_id(cls, value: str) -> str:
        return _require_non_empty(value, "draftId")

    @model_validator(mode="before")
    @classmethod
    def _sync_draft_id(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        if "draftId" in value:
            return value
        draft = value.get("draft")
        if isinstance(draft, Mapping):
            draft_id = draft.get("draftId")
            if isinstance(draft_id, str) and draft_id:
                value = dict(value)
                value["draftId"] = draft_id
        return value

    @model_validator(mode="after")
    def _require_current_bot(self) -> Self:
        _scope_matches_draft(self.scope, self.draft)
        if self.draft_id != self.draft.draft_id:
            raise ValueError("draftId must match draft.draftId")
        if self.saved_scope != "current_bot_draft_store":
            raise ValueError("savedScope must be current_bot_draft_store")
        _reject_true(self.activation_eligibility, "activationEligibility")
        _reject_true(self.activation_enabled, "activationEnabled")
        return self


class CompileRecipePack(_BaseAuthoringContract):
    catalog: CompileRecipePackCatalog | None = None
    result: CompileRecipePackResult | None = None

    @model_validator(mode="after")
    def _validate_result(self) -> Self:
        _require_builder_session_with_draft(self.scope, "CompileRecipePack")
        if self.result is None:
            return self
        if getattr(self.result, "activation_eligibility", False):
            _reject_true(self.result.activation_eligibility, "activationEligibility")
        return self


def run_compile_recipe_pack(
    request: CompileRecipePack | Mapping[str, object],
) -> CompileRecipePack:
    compile_request = (
        request
        if isinstance(request, CompileRecipePack)
        else CompileRecipePack.model_validate(request)
    )
    result = compile_recipe_pack(
        compile_request.scope,
        catalog=compile_request.catalog,
    )
    return compile_request.model_copy(update={"result": result})


class DryRunRecipePack(_BaseAuthoringContract):
    request: DryRunRecipePackRequest | None = None
    catalog: DryRunRecipePackCatalog | None = None
    config: DryRunRecipePackConfig | None = None
    result: DryRunRecipePackResult | None = None

    @model_validator(mode="after")
    def _validate_result(self) -> Self:
        _require_builder_session_with_draft(self.scope, "DryRunRecipePack")
        if self.result is None:
            return self
        _reject_true(self.result.activation_eligibility, "activationEligibility")
        return self


def run_dry_run_recipe_pack(
    request: DryRunRecipePack | Mapping[str, object],
) -> DryRunRecipePack:
    run_request = (
        request
        if isinstance(request, DryRunRecipePack)
        else DryRunRecipePack.model_validate(request)
    )
    result = dry_run_recipe_pack(
        run_request.scope,
        request=run_request.request,
        catalog=run_request.catalog,
        config=run_request.config,
    )
    return run_request.model_copy(update={"result": result})


class GenerateActivationPlan(_BaseAuthoringContract):
    draft: RecipePackDraft
    can_activate: Literal[False] = Field(default=False, alias="canActivate")
    activation_eligibility: Literal[False] = Field(
        default=False,
        alias="activationEligibility",
    )
    activation_plan: tuple[str, ...] = Field(default=(), alias="activationPlan")
    blockers: tuple[str, ...] = ()
    compile_ok: bool = True
    compiled_snapshot_digest: str | None = Field(default=None, alias="compiledSnapshotDigest")

    @field_validator("activation_plan", "blockers")
    @classmethod
    def _validate_action_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _require_non_empty_strings(value, "activation planning details")
        return value

    @model_validator(mode="after")
    def _require_no_activation(self) -> Self:
        builder_session = _require_builder_session_with_draft(
            self.scope,
            "GenerateActivationPlan",
        )
        _scope_matches_draft(self.scope, self.draft)
        if self.draft != builder_session.draft:
            raise ValueError("draft must match RecipeBuilderSession draft")
        _reject_true(self.can_activate, "canActivate")
        _reject_true(self.activation_eligibility, "activationEligibility")
        return self


def run_generate_activation_plan(
    request: GenerateActivationPlan | Mapping[str, object],
) -> GenerateActivationPlan:
    plan_request = (
        request
        if isinstance(request, GenerateActivationPlan)
        else GenerateActivationPlan.model_validate(request)
    )
    compile_result = compile_recipe_pack(
        _require_builder_session_with_draft(
            plan_request.scope,
            "GenerateActivationPlan",
        ),
        catalog=None,
    )

    blockers: list[str] = [] if compile_result.ok else list(compile_result.blocked_reasons)
    if not blockers:
        blockers.append("activation is intentionally disabled in authoring mode")
    activation_plan = tuple(
        () if blockers else ("save_draft", "request_owner_approval")
    )

    return plan_request.model_copy(
        update={
            "compile_ok": compile_result.ok,
            "blockers": tuple(blockers),
            "compiledSnapshotDigest": compile_result.compiled_snapshot_digest,
            "activationPlan": activation_plan,
        }
    )


DraftRecipePackTool = DraftRecipePack
DraftHarnessPolicyTool = DraftHarnessPolicy
