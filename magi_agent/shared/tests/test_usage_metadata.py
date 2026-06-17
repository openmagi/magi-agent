"""G2: shared duck-typed ADK usage-metadata extraction.

The prompt-token extractor is factored out of ``cli/engine.py:_adk_usage_metadata``
so the live context-compaction plugin can read the real prompt-token count of the
just-completed model call WITHOUT engine.py importing any ``google.*`` symbol at
module scope. These tests pin the duck-typed contract (snake_case + camelCase,
Mapping + attribute objects, nested ``llm_response``/``response``) and the
import-clean property.
"""

from __future__ import annotations

from types import SimpleNamespace


def test_prompt_tokens_snake_case_attribute() -> None:
    from magi_agent.shared.usage_metadata import prompt_tokens_from_response

    resp = SimpleNamespace(
        usage_metadata=SimpleNamespace(prompt_token_count=1234)
    )
    assert prompt_tokens_from_response(resp) == 1234


def test_prompt_tokens_camel_case_attribute() -> None:
    from magi_agent.shared.usage_metadata import prompt_tokens_from_response

    resp = SimpleNamespace(usageMetadata=SimpleNamespace(promptTokenCount=99))
    assert prompt_tokens_from_response(resp) == 99


def test_prompt_tokens_mapping() -> None:
    from magi_agent.shared.usage_metadata import prompt_tokens_from_response

    resp = {"usage_metadata": {"prompt_token_count": 555}}
    assert prompt_tokens_from_response(resp) == 555


def test_prompt_tokens_nested_llm_response() -> None:
    from magi_agent.shared.usage_metadata import prompt_tokens_from_response

    resp = SimpleNamespace(
        llm_response=SimpleNamespace(
            usage_metadata=SimpleNamespace(prompt_token_count=42)
        )
    )
    assert prompt_tokens_from_response(resp) == 42


def test_prompt_tokens_absent_returns_none() -> None:
    from magi_agent.shared.usage_metadata import prompt_tokens_from_response

    assert prompt_tokens_from_response(SimpleNamespace(author="model")) is None
    assert prompt_tokens_from_response(None) is None
    assert prompt_tokens_from_response({}) is None


def test_prompt_tokens_zero_is_omitted() -> None:
    # Zero prompt tokens are not a meaningful budget signal -> None (never fabricated).
    from magi_agent.shared.usage_metadata import prompt_tokens_from_response

    resp = SimpleNamespace(usage_metadata=SimpleNamespace(prompt_token_count=0))
    assert prompt_tokens_from_response(resp) is None


def test_prompt_tokens_bool_rejected() -> None:
    from magi_agent.shared.usage_metadata import prompt_tokens_from_response

    resp = SimpleNamespace(usage_metadata=SimpleNamespace(prompt_token_count=True))
    assert prompt_tokens_from_response(resp) is None


def test_prompt_tokens_never_raises_on_hostile_object() -> None:
    from magi_agent.shared.usage_metadata import prompt_tokens_from_response

    class _Boom:
        @property
        def usage_metadata(self):  # noqa: ANN001
            raise RuntimeError("boom")

    # Duck-typed read must never raise into the caller.
    assert prompt_tokens_from_response(_Boom()) is None


def test_shared_module_import_clean_no_google() -> None:
    """The shared extractor must not import ``google.*`` at module scope.

    ``cli/engine.py`` reuses this module; importing it must not drag a
    ``google.*`` symbol into engine's module namespace (asserted separately by
    ``test_engine_import_clean_in_fresh_interpreter``).
    """
    import subprocess
    import sys

    code = (
        "import sys; import magi_agent.shared.usage_metadata as m; "
        "names = [n for n in dir(m) if n.startswith('google')]; "
        "mods = [k for k in sys.modules if k == 'google' or k.startswith('google.')]; "
        "assert not names, names; "
        "import inspect; src = inspect.getsource(m); "
        "assert 'import google' not in src, 'module-scope google import'"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
