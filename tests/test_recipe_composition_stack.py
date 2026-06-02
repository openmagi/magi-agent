from __future__ import annotations

import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from openmagi_core_agent.recipes.composition import RecipeStackInput


def _secret_fixture(*parts: str) -> str:
    return "".join(parts)


DUMMY_SECRET_SUFFIX = _secret_fixture("12345678", "90abcdef")
DUMMY_SK_PROJ = _secret_fixture("sk-", "proj-", DUMMY_SECRET_SUFFIX)
DUMMY_SK_LIVE = _secret_fixture("sk_", "live_", "12345678")
DUMMY_SK_TEST = _secret_fixture("sk_", "test_", "12345678")
DUMMY_SK_ANT = _secret_fixture("sk-", "ant-api03-", "abcdefgh")
DUMMY_XOX = _secret_fixture("xoxc-", "12345678")
DUMMY_RK_LIVE = _secret_fixture("rk_", "live_", DUMMY_SECRET_SUFFIX)
DUMMY_GLPAT = _secret_fixture("glpat-", DUMMY_SECRET_SUFFIX)
DUMMY_GITHUB_PAT = _secret_fixture("github_", "pat_", "12345678")
DUMMY_GHP = _secret_fixture("ghp_", "12345678")
DUMMY_AWS_ACCESS_KEY = _secret_fixture("AKIA", "IOSFODNN", "7EXAMPLE")
DUMMY_GOOGLE_API_KEY = _secret_fixture("AIza", "SyD", "abcdefghijkl", "mnopqrstuvwxyz", "1234567")
DUMMY_JWT = _secret_fixture(
    "eyJ",
    "hbGciOi",
    "JIUzI1NiJ9",
    ".",
    "eyJ",
    "zdWIiOi",
    "IxMjM0NTY3ODkwIn0",
    ".",
    "signature",
)


UNSAFE_RECIPE_REFS = (
    "openmagi.Secret",
    "openmagi research",
    "openmagi/research",
    "../private/recipe",
    "/Users/alice/.config/recipe",
    DUMMY_SK_PROJ,
    f"openmagi.{DUMMY_SK_LIVE}",
    f"openmagi.{DUMMY_SK_TEST}",
    f"openmagi.{DUMMY_SK_ANT}",
    f"openmagi.{DUMMY_XOX}",
    f"openmagi.{DUMMY_RK_LIVE}",
    f"openmagi.{DUMMY_GLPAT}",
    f"openmagi.{DUMMY_GITHUB_PAT}",
    f"openmagi.{DUMMY_GHP}",
    f"openmagi.{DUMMY_AWS_ACCESS_KEY.lower()}",
    f"openmagi.{DUMMY_GOOGLE_API_KEY.lower()}",
    f"openmagi.{DUMMY_JWT}",
    "openmagi.apikey",
    "openmagi.api-key",
    "openmagi.auth",
    "openmagi.session-key",
    "openmagi.accesskey",
    "openmagi.secret-token",
    "openmagi.raw.prompt",
    "openmagi.tool.args",
    "openmagi.tool.results",
    "openmagi.privateconfig",
    "openmagi.pass.word",
    "sk.proj.1234567890",
)

REF_FIELD_NAMES = (
    ("explicitRecipeRefs", "explicit_recipe_refs"),
    ("autoRecipeRefs", "auto_recipe_refs"),
    ("defaultRecipeRefs", "default_recipe_refs"),
    ("pluginRecipeRefs", "plugin_recipe_refs"),
    ("hardSafetyRefs", "hard_safety_refs"),
)


@pytest.mark.parametrize("unsafe_ref", UNSAFE_RECIPE_REFS)
@pytest.mark.parametrize(("alias_name", "snake_name"), REF_FIELD_NAMES)
@pytest.mark.parametrize("field_style", ("alias", "snake"))
def test_unsafe_recipe_refs_are_rejected_from_every_ref_section(
    unsafe_ref: str,
    alias_name: str,
    snake_name: str,
    field_style: str,
) -> None:
    field_name = alias_name if field_style == "alias" else snake_name

    with pytest.raises(ValidationError):
        RecipeStackInput(
            **{
                field_name: [unsafe_ref],
                "turnId": "turn-1",
                "sessionId": "session-1",
            }
        )


def test_unsafe_auto_refs_are_rejected_even_when_auto_recipes_are_disabled() -> None:
    for field_name in ("autoRecipeRefs", "auto_recipe_refs"):
        with pytest.raises(ValidationError):
            RecipeStackInput(
                explicitRecipeRefs=["openmagi.research"],
                **{
                    field_name: ["openmagi.raw.prompt"],
                    "allowAdditionalAutoRecipes": False,
                    "turnId": "turn-1",
                    "sessionId": "session-1",
                },
            )


def test_malformed_ref_containers_are_rejected() -> None:
    malformed_values = (
        {"first": "openmagi.research"},
        b"openmagi.research",
        123,
        ["openmagi.research", 123],
        [1.2],
    )

    for field_name in ("explicitRecipeRefs", "hardSafetyRefs"):
        for value in malformed_values:
            with pytest.raises(ValidationError):
                RecipeStackInput(
                    **{
                        field_name: value,
                        "turnId": "turn-1",
                        "sessionId": "session-1",
                    }
                )


def test_set_ref_containers_reject_non_strings_without_stringifying() -> None:
    class ExplodingString:
        def __str__(self) -> str:
            raise AssertionError("non-string ref was stringified")

    with pytest.raises(ValidationError):
        RecipeStackInput(
            explicitRecipeRefs={"openmagi.research", ExplodingString()},
            turnId="turn-1",
            sessionId="session-1",
        )


def test_mutated_ref_iterable_errors_are_redacted() -> None:
    class LeakyIterable:
        def __iter__(self):
            raise ValueError(f"/Users/alice/private/{DUMMY_SK_PROJ}")

    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.research"],
        turnId="turn-1",
        sessionId="session-1",
    )
    stack.__dict__["explicit_recipe_refs"] = LeakyIterable()

    calls = (
        stack.public_projection,
        stack.stack_digest,
        lambda: stack.model_dump(by_alias=True, mode="json"),
        stack.model_dump_json,
    )
    for call in calls:
        with pytest.raises(Exception) as exc_info:
            call()
        error_text = str(exc_info.value)
        assert "/Users/alice" not in error_text
        assert "sk-proj" not in error_text
        assert DUMMY_SECRET_SUFFIX not in error_text


def test_validation_errors_do_not_echo_secret_shaped_refs() -> None:
    with pytest.raises(ValidationError) as exc_info:
        RecipeStackInput(
            explicitRecipeRefs=[DUMMY_SK_PROJ],
            turnId="turn-1",
            sessionId="session-1",
        )

    error_text = str(exc_info.value)
    assert "sk-proj" not in error_text
    assert DUMMY_SECRET_SUFFIX not in error_text


@pytest.mark.parametrize(
    "field_name",
    ("selectionSource", "turnId", "sessionId"),
)
@pytest.mark.parametrize(
    "unsafe_value",
    (
        "privateConfig",
        "raw:prompt",
        "tool.args",
        "tool-results",
        "credential-ref",
        "apiKey",
        "access-key",
        "session_key",
        "auth",
        DUMMY_SK_PROJ,
        DUMMY_GITHUB_PAT,
        DUMMY_GHP,
        DUMMY_XOX,
        DUMMY_AWS_ACCESS_KEY,
        DUMMY_GOOGLE_API_KEY,
        DUMMY_JWT,
        DUMMY_RK_LIVE,
        DUMMY_GLPAT,
    ),
)
def test_context_identifiers_reject_sensitive_variants(
    field_name: str,
    unsafe_value: str,
) -> None:
    with pytest.raises(ValidationError):
        RecipeStackInput(
            explicitRecipeRefs=["openmagi.research"],
            **{field_name: unsafe_value},
        )


@pytest.mark.parametrize(
    "field_name",
    ("selection_source", "turn_id", "session_id"),
)
def test_model_dump_rejects_mutated_credential_shaped_context(field_name: str) -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.research"],
        selectionSource="client",
        turnId="turn-1",
        sessionId="session-1",
    )
    stack.__dict__[field_name] = DUMMY_AWS_ACCESS_KEY

    with pytest.raises(Exception) as exc_info:
        stack.model_dump(by_alias=True, mode="json")

    assert DUMMY_AWS_ACCESS_KEY not in str(exc_info.value)


@pytest.mark.parametrize(
    "field_name",
    ("selectionSource", "turnId", "sessionId"),
)
@pytest.mark.parametrize("unsafe_value", (b"turn-1", 123, True))
def test_context_identifiers_reject_non_string_inputs(field_name: str, unsafe_value: object) -> None:
    with pytest.raises(ValidationError):
        RecipeStackInput(
            explicitRecipeRefs=["openmagi.research"],
            **{field_name: unsafe_value},
        )


def test_duplicate_refs_are_deduped_by_section_and_explicit_order_is_preserved() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=[
            "openmagi.research",
            "openmagi.coding",
            "openmagi.research",
            "openmagi.office_automation",
        ],
        autoRecipeRefs=["openmagi.coding", "openmagi.research", "openmagi.coding"],
        defaultRecipeRefs=["openmagi.default", "openmagi.default"],
        pluginRecipeRefs=["partner.plugin-alpha", "partner.plugin-alpha"],
        hardSafetyRefs=["openmagi.safety", "openmagi.safety"],
        allowAdditionalAutoRecipes=True,
        turnId="turn-1",
        sessionId="session-1",
    )

    assert stack.explicit_recipe_refs == (
        "openmagi.research",
        "openmagi.coding",
        "openmagi.office_automation",
    )
    assert stack.auto_recipe_refs == ("openmagi.coding", "openmagi.research")
    assert stack.default_recipe_refs == ("openmagi.default",)
    assert stack.plugin_recipe_refs == ("partner.plugin-alpha",)
    assert stack.hard_safety_refs == ("openmagi.safety",)


def test_explicit_refs_are_preserved_ordered_and_auto_refs_are_blocked_when_disabled() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.research", "openmagi.coding"],
        autoRecipeRefs=["openmagi.artifact-delivery"],
        allowAdditionalAutoRecipes=False,
        turnId="turn-1",
        sessionId="session-1",
    )

    assert stack.explicit_recipe_refs == ("openmagi.research", "openmagi.coding")
    assert stack.auto_recipe_refs == ()
    assert stack.all_recipe_refs() == ("openmagi.research", "openmagi.coding")


@pytest.mark.parametrize("false_like", ("false", "f", "n", "no", "off", "0", 0, b"false", b"0"))
def test_false_like_auto_flag_cannot_leave_auto_refs_enabled(false_like: object) -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.research"],
        autoRecipeRefs=["openmagi.artifact-delivery"],
        allowAdditionalAutoRecipes=false_like,
        turnId="turn-1",
        sessionId="session-1",
    )

    assert stack.allow_additional_auto_recipes is False
    assert stack.auto_recipe_refs == ()


@pytest.mark.parametrize("truthy_non_bool", ("true", "t", "yes", "on", "1", 1, b"true", b"1"))
def test_auto_refs_can_only_be_enabled_by_literal_true(truthy_non_bool: object) -> None:
    with pytest.raises(ValidationError):
        RecipeStackInput(
            explicitRecipeRefs=["openmagi.research"],
            autoRecipeRefs=["openmagi.artifact-delivery"],
            allowAdditionalAutoRecipes=truthy_non_bool,
            turnId="turn-1",
            sessionId="session-1",
        )


def test_hard_safety_refs_are_retained_when_duplicated_elsewhere_or_auto_disabled() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.safety", "openmagi.research"],
        autoRecipeRefs=["openmagi.safety"],
        hardSafetyRefs=["openmagi.safety", "openmagi.hard-blocks"],
        allowAdditionalAutoRecipes=False,
        turnId="turn-1",
        sessionId="session-1",
    )

    assert stack.auto_recipe_refs == ()
    assert stack.hard_safety_refs == ("openmagi.safety", "openmagi.hard-blocks")
    assert stack.all_recipe_refs() == (
        "openmagi.safety",
        "openmagi.research",
        "openmagi.safety",
        "openmagi.hard-blocks",
    )


def test_client_provided_refs_remain_untrusted_until_admission() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.research"],
        pluginRecipeRefs=["partner.plugin-alpha"],
        selectionSource="client",
        turnId="turn-1",
        sessionId="session-1",
    )

    assert stack.trusted is False
    assert stack.admitted is False
    projection = stack.public_projection()
    assert projection["trusted"] is False
    assert projection["admitted"] is False
    assert projection["refsTrustState"] == "untrusted_until_admission"


def test_public_projection_and_digest_do_not_reflect_forged_authority_flags() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.research"],
        selectionSource="client",
        turnId="turn-1",
        sessionId="session-1",
    )
    clean_digest = stack.stack_digest()

    stack.__dict__["trusted"] = True
    stack.__dict__["admitted"] = True
    stack.__dict__["default_off"] = False

    assert stack.public_projection()["trusted"] is False
    assert stack.public_projection()["admitted"] is False
    assert stack.public_projection()["defaultOff"] is True
    assert stack.stack_digest() == clean_digest
    assert stack.model_dump(by_alias=True)["trusted"] is False
    assert stack.model_dump(by_alias=True)["admitted"] is False
    assert stack.model_dump(by_alias=True)["defaultOff"] is True


@pytest.mark.parametrize("method_name", ("all_recipe_refs", "stack_digest", "public_projection"))
def test_public_outputs_revalidate_mutated_ref_sections(method_name: str) -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.research"],
        selectionSource="client",
        turnId="turn-1",
        sessionId="session-1",
    )
    stack.__dict__["explicit_recipe_refs"] = ("openmagi.raw.prompt",)

    with pytest.raises(ValueError):
        getattr(stack, method_name)()


def test_model_dump_rejects_mutated_secret_shaped_ref_sections() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.research"],
        selectionSource="client",
        turnId="turn-1",
        sessionId="session-1",
    )
    stack.__dict__["plugin_recipe_refs"] = ("openmagi.sk_live_12345678",)

    with pytest.raises(Exception) as exc_info:
        stack.model_dump(by_alias=True, mode="json")

    error_text = str(exc_info.value)
    assert "sk_live" not in error_text
    assert "12345678" not in error_text


def test_copy_and_construct_reject_secret_shaped_ref_sections() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.research"],
        turnId="turn-1",
        sessionId="session-1",
    )

    with pytest.raises(ValidationError):
        stack.model_copy(update={"pluginRecipeRefs": ["openmagi.sk_test_12345678"]})

    with pytest.raises(ValidationError):
        RecipeStackInput.model_construct(
            explicitRecipeRefs=["openmagi.sk-ant-api03-abcdefgh"],
            turnId="turn-1",
            sessionId="session-1",
        )


@pytest.mark.parametrize("method_name", ("stack_digest", "public_projection"))
def test_public_outputs_revalidate_mutated_context_fields(method_name: str) -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.research"],
        selectionSource="client",
        turnId="turn-1",
        sessionId="session-1",
    )
    stack.__dict__["turn_id"] = DUMMY_SK_PROJ

    with pytest.raises(ValueError) as exc_info:
        getattr(stack, method_name)()

    error_text = str(exc_info.value)
    assert "sk-proj" not in error_text
    assert DUMMY_SECRET_SUFFIX not in error_text


@pytest.mark.parametrize("method_name", ("stack_digest", "public_projection"))
def test_public_outputs_revalidate_mutated_auto_flag(method_name: str) -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.research"],
        allowAdditionalAutoRecipes=False,
        turnId="turn-1",
        sessionId="session-1",
    )
    stack.__dict__["allow_additional_auto_recipes"] = "true"

    with pytest.raises(ValueError):
        getattr(stack, method_name)()


def test_public_outputs_suppress_mutated_auto_refs_when_auto_flag_is_disabled() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.research"],
        allowAdditionalAutoRecipes=False,
        turnId="turn-1",
        sessionId="session-1",
    )
    clean_digest = stack.stack_digest()
    stack.__dict__["auto_recipe_refs"] = ("openmagi.auto",)

    assert stack.public_projection()["autoRecipeRefs"] == ()
    assert stack.stack_digest() == clean_digest
    assert stack.model_dump(by_alias=True, mode="json")["autoRecipeRefs"] == []


def test_public_outputs_suppress_existing_auto_refs_when_auto_flag_is_mutated_false() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.research"],
        autoRecipeRefs=["openmagi.auto"],
        allowAdditionalAutoRecipes=True,
        turnId="turn-1",
        sessionId="session-1",
    )
    stack.__dict__["allow_additional_auto_recipes"] = False

    assert stack.all_recipe_refs() == ("openmagi.research",)
    assert stack.public_projection()["autoRecipeRefs"] == ()
    assert stack.model_dump(by_alias=True, mode="json")["autoRecipeRefs"] == []


@pytest.mark.parametrize("false_like", ("false", "0", 0, b"false"))
def test_public_outputs_treat_mutated_false_like_auto_flag_as_disabled(false_like: object) -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.research"],
        autoRecipeRefs=["openmagi.auto"],
        allowAdditionalAutoRecipes=True,
        turnId="turn-1",
        sessionId="session-1",
    )
    stack.__dict__["allow_additional_auto_recipes"] = false_like

    assert stack.all_recipe_refs() == ("openmagi.research",)
    assert stack.public_projection()["allowAdditionalAutoRecipes"] is False
    assert stack.public_projection()["autoRecipeRefs"] == ()
    dumped = stack.model_dump(by_alias=True, mode="json")
    assert dumped["allowAdditionalAutoRecipes"] is False
    assert dumped["autoRecipeRefs"] == []


def test_model_dump_revalidates_mutated_ref_and_context_sections() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.research"],
        selectionSource="client",
        turnId="turn-1",
        sessionId="session-1",
    )
    stack.__dict__["explicit_recipe_refs"] = (DUMMY_SK_PROJ,)
    stack.__dict__["allow_additional_auto_recipes"] = "true"
    stack.__dict__["session_id"] = "/Users/alice/private/session"

    with pytest.raises(Exception) as exc_info:
        stack.model_dump(by_alias=True, mode="json")

    error_text = str(exc_info.value)
    assert "sk-proj" not in error_text
    assert DUMMY_SECRET_SUFFIX not in error_text
    assert "/Users/alice" not in error_text


def test_model_validate_revalidates_existing_instances() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.research"],
        turnId="turn-1",
        sessionId="session-1",
    )
    stack.__dict__["hard_safety_refs"] = ("openmagi.apikey",)

    with pytest.raises(ValidationError):
        RecipeStackInput.model_validate(stack)


def test_default_off_is_true_and_cannot_be_set_false() -> None:
    stack = RecipeStackInput(turnId="turn-1", sessionId="session-1")

    assert stack.default_off is True
    with pytest.raises(ValidationError):
        RecipeStackInput(defaultOff=False, turnId="turn-1", sessionId="session-1")


def test_public_projection_is_digest_safe_and_public_only() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.research"],
        pluginRecipeRefs=["partner.plugin-alpha"],
        selectionSource="client",
        turnId="turn-1",
        sessionId="session-1",
    )

    projection = stack.public_projection()
    dumped = json.dumps(projection, sort_keys=True)

    assert projection["stackDigest"] == stack.stack_digest()
    assert projection["explicitRecipeRefs"] == ("openmagi.research",)
    assert "rawPrompt" not in dumped
    assert "privateConfig" not in dumped
    assert "toolArgs" not in dumped
    assert "toolResults" not in dumped
    assert "sk-proj-" not in dumped
    assert "/Users/" not in dumped


def test_importing_composition_module_does_not_load_runtime_toolhost_or_transport() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.recipes.composition")
assert hasattr(module, "RecipeStackInput")

forbidden_prefixes = (
    "google.adk",
    "openmagi_core_agent.adk_bridge",
    "openmagi_core_agent.browser",
    "openmagi_core_agent.channels",
    "openmagi_core_agent.memory",
    "openmagi_core_agent.runtime",
    "openmagi_core_agent.routing",
    "openmagi_core_agent.tools",
    "openmagi_core_agent.toolhost",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.workspace",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"recipe composition import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
