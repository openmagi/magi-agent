"""Core execute/search tools must expose real parameter schemas + usage docs.

PR #175 fixed FileRead/FileWrite/FileEdit, but Bash/TestRun/Grep/Glob/PatchApply
still shipped the permissive ``{"additionalProperties": true}`` placeholder and
one-line descriptions — the model had to guess parameter names and never
learned when/how to use each tool (e.g. TestRun's 300s budget for test suites
vs Bash's short command budget).
"""

from magi_agent.tools.catalog import _CORE_TOOL_MANIFESTS


def _manifest(name: str):
    for manifest in _CORE_TOOL_MANIFESTS:
        if manifest.name == name:
            return manifest
    raise AssertionError(f"manifest not found: {name}")


def _properties(name: str) -> dict:
    schema = _manifest(name).input_schema
    assert isinstance(schema, dict)
    properties = schema.get("properties")
    assert isinstance(properties, dict), f"{name} has no declared properties"
    return properties


def test_bash_schema_declares_command() -> None:
    properties = _properties("Bash")
    assert "command" in properties
    assert "command" in _manifest("Bash").input_schema.get("required", [])


def test_bash_description_documents_semantics() -> None:
    description = _manifest("Bash").description.lower()
    assert "workspace root" in description or "working directory" in description
    assert "timeout" in description
    assert "testrun" in description  # steer long test runs to the right tool


def test_testrun_schema_and_description() -> None:
    properties = _properties("TestRun")
    assert "command" in properties
    description = _manifest("TestRun").description.lower()
    assert "test" in description
    assert "300" in _manifest("TestRun").description  # advertise the budget


def test_grep_schema_declares_pattern_and_glob() -> None:
    properties = _properties("Grep")
    assert "pattern" in properties
    assert "glob" in properties


def test_glob_schema_declares_pattern() -> None:
    properties = _properties("Glob")
    assert "pattern" in properties


def test_patch_apply_schema_declares_both_shapes() -> None:
    properties = _properties("PatchApply")
    assert "patch" in properties
    assert "path" in properties
    assert "content" in properties
