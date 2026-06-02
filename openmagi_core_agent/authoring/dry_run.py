from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from openmagi_core_agent.authoring.compiler import (
    CompileRecipePackCatalog,
    CompileRecipePackDiagnostic,
    compile_recipe_pack,
)
from openmagi_core_agent.authoring.contracts import RecipeBuilderSession


_MODEL_CONFIG = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")


class _DryRunModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for authoring dry-run contracts")

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


class DryRunRecipePackCatalog(_DryRunModel):
    route_refs: tuple[str, ...] = Field(default=(), alias="routeRefs")
    general_chat_route_ref: str = Field(
        default="route.general_chat",
        alias="generalChatRouteRef",
    )
    compiler_catalog: CompileRecipePackCatalog | None = Field(
        default=None,
        alias="compilerCatalog",
    )

    @field_validator("route_refs")
    @classmethod
    def _validate_route_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _require_non_empty_strings(value, "route refs")
        return value

    @field_validator("general_chat_route_ref")
    @classmethod
    def _validate_general_chat_route_ref(cls, value: str) -> str:
        return _require_non_empty(value, "generalChatRouteRef")

    @classmethod
    def default(cls) -> DryRunRecipePackCatalog:
        return cls(compilerCatalog=CompileRecipePackCatalog.default())


class DryRunRecipePackConfig(_DryRunModel):
    no_match_terminal_state: str = Field(
        default="ask_user_for_route",
        alias="noMatchTerminalState",
    )

    @field_validator("no_match_terminal_state")
    @classmethod
    def _validate_terminal_state(cls, value: str) -> str:
        return _require_non_empty(value, "noMatchTerminalState")


class DryRunRecipePackRequest(_DryRunModel):
    requested_route_ref: str | None = Field(default=None, alias="requestedRouteRef")
    governed_task: bool = Field(default=True, alias="governedTask")

    @field_validator("requested_route_ref")
    @classmethod
    def _validate_requested_route_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_non_empty(value, "requestedRouteRef")


class DryRunRecipePackWarning(_DryRunModel):
    code: str
    message: str
    path: str | None = None
    ref: str | None = None


class DryRunRecipePackResult(_DryRunModel):
    ok: bool
    selected_route: str | None = Field(default=None, alias="selectedRoute")
    context_projection: str | None = Field(default=None, alias="contextProjection")
    expected_tools: tuple[str, ...] = Field(default=(), alias="expectedTools")
    expected_evidence: tuple[str, ...] = Field(default=(), alias="expectedEvidence")
    expected_validators: tuple[str, ...] = Field(default=(), alias="expectedValidators")
    expected_approvals: tuple[str, ...] = Field(default=(), alias="expectedApprovals")
    predicted_terminal_states: tuple[str, ...] = Field(
        default=(),
        alias="predictedTerminalStates",
    )
    denied_actions: tuple[str, ...] = Field(default=(), alias="deniedActions")
    warnings: tuple[DryRunRecipePackWarning, ...] = ()
    activation_eligibility: Literal[False] = Field(
        default=False,
        alias="activationEligibility",
    )


def dry_run_recipe_pack(
    session: RecipeBuilderSession | Mapping[str, object],
    *,
    request: DryRunRecipePackRequest | Mapping[str, object] | None = None,
    catalog: DryRunRecipePackCatalog | Mapping[str, object] | None = None,
    config: DryRunRecipePackConfig | Mapping[str, object] | None = None,
) -> DryRunRecipePackResult:
    dry_run_request = (
        request
        if isinstance(request, DryRunRecipePackRequest)
        else DryRunRecipePackRequest.model_validate(request or {})
    )
    dry_run_catalog = (
        catalog
        if isinstance(catalog, DryRunRecipePackCatalog)
        else DryRunRecipePackCatalog.model_validate(catalog or {})
    )
    dry_run_config = (
        config
        if isinstance(config, DryRunRecipePackConfig)
        else DryRunRecipePackConfig.model_validate(config or {})
    )

    try:
        builder_session = (
            session
            if isinstance(session, RecipeBuilderSession)
            else RecipeBuilderSession.model_validate(session)
        )
    except ValidationError as exc:
        return _blocked_result(
            denied_actions=("invalid_recipe_builder_scope",),
            warnings=(
                DryRunRecipePackWarning(
                    code="invalid_recipe_builder_scope",
                    message=str(exc),
                    path="session",
                ),
            ),
        )

    compiler_result = compile_recipe_pack(
        builder_session,
        catalog=dry_run_catalog.compiler_catalog,
    )
    compile_warnings = _compiler_warnings(compiler_result.diagnostics)
    if not compiler_result.ok:
        return _blocked_result(
            context_projection=_projection_mode(builder_session),
            denied_actions=compiler_result.blocked_reasons,
            warnings=compile_warnings,
        )

    draft = builder_session.draft
    if draft is None:
        return _blocked_result(
            denied_actions=("missing_recipe_pack_draft",),
            warnings=(
                DryRunRecipePackWarning(
                    code="missing_recipe_pack_draft",
                    message="DryRunRecipePack requires a draft recipe pack.",
                    path="draft",
                ),
            ),
        )

    pack = draft.pack
    selected_route = _select_route(
        requested_route=dry_run_request.requested_route_ref,
        recipe_refs=pack.recipe_refs,
        route_refs=dry_run_catalog.route_refs,
        general_chat_route=dry_run_catalog.general_chat_route_ref,
    )
    if selected_route is None:
        denied_actions = ["no_route_match"]
        warnings = [
            DryRunRecipePackWarning(
                code="no_route_match",
                message="No authored recipe route matched the dry-run request.",
                path="draft.pack.recipeRefs",
                ref=dry_run_request.requested_route_ref,
            )
        ]
        if _is_general_chat_request(
            dry_run_request.requested_route_ref,
            dry_run_catalog.general_chat_route_ref,
        ):
            denied_actions.append("general_chat_fallback_denied")
            warnings.append(
                DryRunRecipePackWarning(
                    code="general_chat_fallback_denied",
                    message="A governed task cannot silently fall back to general chat.",
                    ref=dry_run_catalog.general_chat_route_ref,
                )
            )
        elif dry_run_request.governed_task:
            denied_actions.append("general_chat_fallback_denied")
            warnings.append(
                DryRunRecipePackWarning(
                    code="general_chat_fallback_denied",
                    message="A governed task without a route must stop at the configured terminal state.",
                    ref=dry_run_catalog.general_chat_route_ref,
                )
            )
        return _blocked_result(
            context_projection=pack.projection_policy.mode,
            predicted_terminal_states=(dry_run_config.no_match_terminal_state,),
            denied_actions=tuple(denied_actions),
            warnings=tuple(warnings),
        )

    return DryRunRecipePackResult(
        ok=True,
        selectedRoute=selected_route,
        contextProjection=pack.projection_policy.mode,
        expectedTools=pack.tool_policy.allowed_tool_refs,
        expectedEvidence=pack.evidence_policy.required_evidence_refs,
        expectedValidators=pack.validator_policy.validator_refs,
        expectedApprovals=(
            pack.approval_policy.authority_refs
            if pack.approval_policy.requires_human_review
            else ()
        ),
        predictedTerminalStates=pack.repair_policy.terminal_states,
        deniedActions=pack.tool_policy.denied_tool_refs,
        warnings=compile_warnings,
        activationEligibility=False,
    )


def _select_route(
    *,
    requested_route: str | None,
    recipe_refs: tuple[str, ...],
    route_refs: tuple[str, ...],
    general_chat_route: str,
) -> str | None:
    route_set = set(route_refs)
    if requested_route is not None:
        if requested_route == general_chat_route:
            return None
        if requested_route in recipe_refs and requested_route in route_set:
            return requested_route
        return None
    for recipe_ref in recipe_refs:
        if recipe_ref in route_set:
            return recipe_ref
    return None


def _is_general_chat_request(requested_route: str | None, general_chat_route: str) -> bool:
    return requested_route == general_chat_route


def _projection_mode(session: RecipeBuilderSession) -> str | None:
    if session.draft is None:
        return None
    return session.draft.pack.projection_policy.mode


def _blocked_result(
    *,
    context_projection: str | None = None,
    predicted_terminal_states: tuple[str, ...] = (),
    denied_actions: tuple[str, ...],
    warnings: tuple[DryRunRecipePackWarning, ...],
) -> DryRunRecipePackResult:
    return DryRunRecipePackResult(
        ok=False,
        selectedRoute=None,
        contextProjection=context_projection,
        predictedTerminalStates=predicted_terminal_states,
        deniedActions=denied_actions,
        warnings=warnings,
        activationEligibility=False,
    )


def _compiler_warnings(
    diagnostics: tuple[CompileRecipePackDiagnostic, ...],
) -> tuple[DryRunRecipePackWarning, ...]:
    return tuple(
        DryRunRecipePackWarning(
            code=diagnostic.code,
            message=diagnostic.message,
            path=diagnostic.path,
            ref=diagnostic.ref,
        )
        for diagnostic in diagnostics
    )


def _copy_update_alias(model_type: type[BaseModel], key: str) -> str:
    field = model_type.model_fields.get(key)
    if field is not None and field.alias is not None:
        return field.alias
    return key


def _require_non_empty(value: str, field_label: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_label} must be non-empty")
    return value


def _require_non_empty_strings(values: tuple[str, ...], field_label: str) -> None:
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"{field_label} must contain non-empty strings")
