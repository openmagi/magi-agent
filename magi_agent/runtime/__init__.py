from __future__ import annotations

_LAZY_EXPORTS = {
    "ChildRunnerAuthorityFlags": (".child_runner_boundary", "ChildRunnerAuthorityFlags"),
    "ChildRunnerConfig": (".child_runner_boundary", "ChildRunnerConfig"),
    "ChildRunnerEnvelopeRef": (".child_runner_boundary", "ChildRunnerEnvelopeRef"),
    "ChildRunnerResult": (".child_runner_boundary", "ChildRunnerResult"),
    "ChildTaskRequest": (".child_runner_boundary", "ChildTaskRequest"),
    "CustomerConfigurablePolicy": (".deterministic_policy", "CustomerConfigurablePolicy"),
    "DeterministicPolicy": (".deterministic_policy", "DeterministicPolicy"),
    "GovernedArtifact": (".governed_projection", "GovernedArtifact"),
    "GovernedClaim": (".governed_projection", "GovernedClaim"),
    "GovernedDraft": (".governed_projection", "GovernedDraft"),
    "LocalChildRunnerBoundary": (".child_runner_boundary", "LocalChildRunnerBoundary"),
    "OpenMagiRuntime": (".openmagi_runtime", "OpenMagiRuntime"),
    "RuntimeAdmissionIssue": (".admission", "RuntimeAdmissionIssue"),
    "RuntimeAdmissionRequest": (".admission", "RuntimeAdmissionRequest"),
    "RuntimeAdmissionResult": (".admission", "RuntimeAdmissionResult"),
    "RuntimeAdmissionStatus": (".admission", "RuntimeAdmissionStatus"),
    "EffectivePolicySnapshot": (".policy_snapshot", "EffectivePolicySnapshot"),
    "LongRunningActivityAuthorityFlags": (
        ".long_running_activity",
        "LongRunningActivityAuthorityFlags",
    ),
    "LongRunningActivityConfig": (".long_running_activity", "LongRunningActivityConfig"),
    "LongRunningActivityPolicy": (".long_running_activity", "LongRunningActivityPolicy"),
    "LongRunningActivityReceipt": (".long_running_activity", "LongRunningActivityReceipt"),
    "LongRunningActivityRequest": (".long_running_activity", "LongRunningActivityRequest"),
    "LongRunningActivityResult": (".long_running_activity", "LongRunningActivityResult"),
    "PolicyDecisionBinding": (".policy_snapshot", "PolicyDecisionBinding"),
    "PolicyDecisionVerdict": (".policy_snapshot", "PolicyDecisionVerdict"),
    "PolicySourceRef": (".policy_snapshot", "PolicySourceRef"),
    "ProjectionDecision": (".governed_projection", "ProjectionDecision"),
    "ProjectionPolicy": (".governed_projection", "ProjectionPolicy"),
    "ProjectionRenderer": (".governed_projection", "ProjectionRenderer"),
    "RuntimeInvariantDecision": (".deterministic_policy", "RuntimeInvariantDecision"),
    "RuntimeInvariantSet": (".deterministic_policy", "RuntimeInvariantSet"),
    "evaluate_runtime_invariants": (
        ".deterministic_policy",
        "evaluate_runtime_invariants",
    ),
    "build_effective_policy_snapshot": (
        ".policy_snapshot",
        "build_effective_policy_snapshot",
    ),
    "digest_policy_snapshot_payload": (
        ".policy_snapshot",
        "digest_policy_snapshot_payload",
    ),
    "digest_compiled_snapshot_payload": (
        ".admission",
        "digest_compiled_snapshot_payload",
    ),
    "runtime_admission_check": (".admission", "runtime_admission_check"),
}

__all__ = sorted(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    from importlib import import_module

    if name not in _LAZY_EXPORTS:
        # Test-isolation fallback: when a sibling test pops this package out of
        # ``sys.modules`` and a later submodule import re-creates a fresh parent
        # package object, submodules that were previously bound as attributes
        # are gone. Re-import the real submodule on demand so attribute access
        # (e.g. ``magi_agent.runtime.governed_turn``) stays order/worker
        # independent. Never import eagerly; skip dunder/private names.
        if name.startswith("_"):
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        try:
            submodule = import_module(f".{name}", __name__)
        except ImportError as exc:
            raise AttributeError(
                f"module {__name__!r} has no attribute {name!r}"
            ) from exc
        globals()[name] = submodule
        return submodule

    module_name, attr_name = _LAZY_EXPORTS[name]
    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
