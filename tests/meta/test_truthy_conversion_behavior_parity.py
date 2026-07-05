"""I-2 PR A behaviour-parity tests for each converted denylist site.

For every site converted from the dangerous denylist semantic to the strict
allowlist semantic in I-2 PR A, this test asserts:

* The correct-spelling values (``"1"`` / ``"true"`` / ``"yes"`` / ``"on"``)
  still enable the gate (UNCHANGED behaviour).
* Explicit falsey values (``"0"`` / ``"false"`` / ``"no"`` / ``"off"``)
  disable the gate (UNCHANGED behaviour).
* Unknown / mis-spelled values (``"disabled"`` / ``"random_garbage"`` /
  ``"enabled"``) now disable the gate (CHANGED — was True under denylist).
* Empty string disables the gate (UNCHANGED — was treated as False by the
  guard ``bool(raw) and ...`` clause in the original denylist sites).
* Unset reads as the documented default for that gate.

These are *parity* tests for the security correction, not coverage of the
gate's downstream effect. Each test simply calls the reader and asserts the
boolean return.

Sites covered
-------------
* ``cli/headless._cli_enabled``                      (default-ON)
* ``cli/engine._runner_policy_routing_enabled``      (default-OFF)
* ``cli/engine._runner_policy_route_blocking_enabled`` (default-OFF)
* ``cli/engine._recipe_intent_binding_enabled``      (profile_bool, now covered by TestProfileAwareDefaultOnReaders)
* ``cli/wiring.local_runner_policy_routing_enabled_from_env`` (default-OFF)
* ``cli/wiring._first_party_tools_enabled``           (default-ON)
* ``harness/self_review._self_review_shadow``         (default-ON)
* ``harness/self_review_pipeline._shadow_mode``       (default-ON)
* ``gateway/daemon.is_gateway_daemon_enabled``        (default-OFF)
* ``gateway/watchers.is_scheduler_executor_enabled``  (default-OFF)
* ``gateway/watchers.is_work_queue_executor_enabled`` (default-OFF)
* ``gateway/watchers._background_live_runner_enabled``(default-OFF)
* ``transport/chat_routes._background_inject_consumer_enabled`` (default-OFF)
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Lazy importers — many of these modules import ADK / heavy deps at module
# top, so each test imports just-in-time and patches the relevant env var
# via monkeypatch.
# ---------------------------------------------------------------------------


_TRUTHY_INPUTS = ("1", "true", "yes", "on", "TRUE", "Yes")
_EXPLICIT_FALSEY_INPUTS = ("0", "false", "no", "off", "FALSE")
# These were ON under the dangerous denylist and now (correctly) OFF for flag_bool.
_UNKNOWN_INPUTS = ("disabled", "enabled", "random_garbage", "yes please", " ", "")
# For profile_bool: unrecognized non-empty values fall back to the non-safe profile
# default (ON). Empty/whitespace-only values are still treated as explicit falsy.
_PROFILE_UNRECOGNIZED_ON = ("disabled", "enabled", "random_garbage", "yes please")
_PROFILE_EMPTY_FALSE = (" ", "")


# ---------------------------------------------------------------------------
# Default-OFF gates: unset → False, truthy → True, falsey/unknown → False.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("module_path", "attr", "env_name"),
    [
        (
            "magi_agent.cli.engine",
            "_runner_policy_routing_enabled",
            "MAGI_RUNNER_POLICY_ROUTING_ENABLED",
        ),
        (
            "magi_agent.cli.engine",
            "_runner_policy_route_blocking_enabled",
            "MAGI_RUNNER_POLICY_ROUTE_BLOCKING_ENABLED",
        ),
        # _recipe_intent_binding_enabled is profile_bool (default-ON under non-safe
        # profiles). It is covered by TestProfileAwareDefaultOnReaders below.
        (
            "magi_agent.cli.wiring",
            "local_runner_policy_routing_enabled_from_env",
            "MAGI_RUNNER_POLICY_ROUTING_ENABLED",
        ),
        (
            "magi_agent.gateway.daemon",
            "is_gateway_daemon_enabled",
            "MAGI_GATEWAY_DAEMON_ENABLED",
        ),
        (
            "magi_agent.gateway.watchers",
            "is_scheduler_executor_enabled",
            "MAGI_SCHEDULER_EXECUTOR_ENABLED",
        ),
        # MAGI_WORK_QUEUE_EXECUTOR_ENABLED promoted to profile-aware default-ON
        # (_pb) under the no-default-off policy; no longer a default-OFF reader.
        (
            "magi_agent.gateway.watchers",
            "_background_live_runner_enabled",
            "MAGI_BACKGROUND_LIVE_RUNNER_ENABLED",
        ),
        (
            "magi_agent.transport.chat_routes",
            "_background_inject_consumer_enabled",
            "MAGI_BACKGROUND_TASK_INJECT_CONSUMER_ENABLED",
        ),
    ],
)
class TestDefaultOffReaders:
    def _read(self, module_path: str, attr: str) -> bool:
        import importlib

        module = importlib.import_module(module_path)
        return getattr(module, attr)()

    def test_unset_reads_false(self, module_path: str, attr: str, env_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(env_name, raising=False)
        assert self._read(module_path, attr) is False

    @pytest.mark.parametrize("value", _TRUTHY_INPUTS)
    def test_truthy_reads_true(self, module_path: str, attr: str, env_name: str, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(env_name, value)
        assert self._read(module_path, attr) is True

    @pytest.mark.parametrize("value", _EXPLICIT_FALSEY_INPUTS)
    def test_explicit_falsey_reads_false(self, module_path: str, attr: str, env_name: str, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(env_name, value)
        assert self._read(module_path, attr) is False

    @pytest.mark.parametrize("value", _UNKNOWN_INPUTS)
    def test_unknown_value_reads_false_was_true_under_denylist(
        self,
        module_path: str,
        attr: str,
        env_name: str,
        value: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The I-2 PR A security correction: unknown / mis-typed values that
        the original denylist semantic would have silently treated as ON now
        correctly read as OFF (because they are not in the allowlist)."""
        monkeypatch.setenv(env_name, value)
        assert self._read(module_path, attr) is False


# ---------------------------------------------------------------------------
# Default-ON gates: unset → True, truthy → True, falsey/unknown → False.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("module_path", "attr", "env_name"),
    [
        (
            "magi_agent.cli.headless",
            "_cli_enabled",
            "MAGI_CLI_ENABLED",
        ),
        (
            "magi_agent.cli.wiring",
            "_first_party_tools_enabled",
            "MAGI_FIRST_PARTY_TOOLS_ENABLED",
        ),
        (
            "magi_agent.harness.self_review",
            "_self_review_shadow",
            "MAGI_SELF_REVIEW_SHADOW",
        ),
        (
            "magi_agent.harness.self_review_pipeline",
            "_shadow_mode",
            "MAGI_SELF_REVIEW_SHADOW",
        ),
    ],
)
class TestDefaultOnReaders:
    def _read(self, module_path: str, attr: str) -> bool:
        import importlib

        module = importlib.import_module(module_path)
        return getattr(module, attr)()

    def test_unset_reads_true(self, module_path: str, attr: str, env_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(env_name, raising=False)
        assert self._read(module_path, attr) is True

    @pytest.mark.parametrize("value", _TRUTHY_INPUTS)
    def test_truthy_reads_true(self, module_path: str, attr: str, env_name: str, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(env_name, value)
        assert self._read(module_path, attr) is True

    @pytest.mark.parametrize("value", _EXPLICIT_FALSEY_INPUTS)
    def test_explicit_falsey_reads_false(self, module_path: str, attr: str, env_name: str, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(env_name, value)
        assert self._read(module_path, attr) is False

    @pytest.mark.parametrize("value", _UNKNOWN_INPUTS)
    def test_unknown_value_reads_false_was_true_under_denylist(
        self,
        module_path: str,
        attr: str,
        env_name: str,
        value: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """I-2 PR A: unknown / mis-spelled values that the original denylist
        semantic would have silently treated as ON now correctly read as OFF.

        For default-ON gates this is the meaningful change: a mis-configured
        value like ``MAGI_X=enabled`` no longer accidentally keeps the gate
        on; the operator must use the canonical truthy spelling to keep it on.
        """
        monkeypatch.setenv(env_name, value)
        assert self._read(module_path, attr) is False


# ---------------------------------------------------------------------------
# Profile-aware default-ON gates (flag_profile_bool, kind="profile_bool").
#
# Contract differs from flag_bool:
#   unset                        -> True  (non-safe profile default)
#   truthy ("1"/"true"/...)      -> True
#   explicit falsy ("0"/"false") -> False
#   unrecognized non-empty       -> True  (profile default, NOT strict-False)
#   empty / whitespace-only      -> False (treated as explicit falsy after trim)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("module_path", "attr", "env_name"),
    [
        (
            "magi_agent.cli.engine",
            "_recipe_intent_binding_enabled",
            "MAGI_RECIPE_INTENT_BINDING_ENABLED",
        ),
    ],
)
class TestProfileAwareDefaultOnReaders:
    def _read(self, module_path: str, attr: str) -> bool:
        import importlib

        module = importlib.import_module(module_path)
        return getattr(module, attr)()

    def test_unset_reads_true(
        self,
        module_path: str,
        attr: str,
        env_name: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Unset under a non-safe profile uses the profile default: ON.
        monkeypatch.delenv(env_name, raising=False)
        assert self._read(module_path, attr) is True

    @pytest.mark.parametrize("value", _TRUTHY_INPUTS)
    def test_truthy_reads_true(
        self,
        module_path: str,
        attr: str,
        env_name: str,
        value: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(env_name, value)
        assert self._read(module_path, attr) is True

    @pytest.mark.parametrize("value", _EXPLICIT_FALSEY_INPUTS)
    def test_explicit_falsey_reads_false(
        self,
        module_path: str,
        attr: str,
        env_name: str,
        value: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(env_name, value)
        assert self._read(module_path, attr) is False

    @pytest.mark.parametrize("value", _PROFILE_UNRECOGNIZED_ON)
    def test_unrecognized_value_reads_true_profile_default(
        self,
        module_path: str,
        attr: str,
        env_name: str,
        value: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """profile_bool: unrecognized non-empty values fall back to the non-safe
        profile default (ON). This is different from flag_bool which treats them
        as strict-False."""
        monkeypatch.setenv(env_name, value)
        assert self._read(module_path, attr) is True

    @pytest.mark.parametrize("value", _PROFILE_EMPTY_FALSE)
    def test_empty_or_whitespace_reads_false(
        self,
        module_path: str,
        attr: str,
        env_name: str,
        value: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Empty string and whitespace-only are treated as explicit falsy.
        monkeypatch.setenv(env_name, value)
        assert self._read(module_path, attr) is False
