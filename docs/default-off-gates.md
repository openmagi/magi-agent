# Default-Off Gates

How to use default-off rollout gates to safely activate new runtime authority.

New runtime authority starts default-off and is enabled only after contract tests, replay, and security review pass.

## The default-off design philosophy

Every boundary module in Magi Agent starts in a disabled state. No boundary can block, modify, or gate agent behavior until it is explicitly enabled through configuration and passes staged validation. This is a structural decision: the runtime ships safe-by-default because untested enforcement is worse than no enforcement.

The default-off posture means a fresh install runs without any active boundary enforcement. All evidence is collected (the ledger always records), but no contract verdicts block output and no boundary decisions prevent side effects. Enforcement is layered on through explicit configuration.

## Authority flags as structural enforcement

The strongest form of default-off is the Literal[False] authority flag. In EvidenceRolloutMetadata (evidence/rollout.py), the fields traffic_attached and execution_attached are typed as Literal[False]. This means the Python type system itself prevents these fields from being set to True through configuration alone. Enabling live authority requires a code change in the boundary module, not a config change.

This pattern ensures that even if someone misconfigures the runtime, authority cannot be accidentally escalated. The type checker will reject any attempt to assign True to a Literal[False] field.

## Configuration hierarchy: disabled to production

Boundaries move through a planned three-stage activation hierarchy. Stage 1 (disabled): the boundary exists in code but is not active. Evidence is collected but no decisions are enforced. Stage 2 (local fake): the boundary is enabled with local_fake_evaluation_enabled, producing real decision types but without live authority. This is the testing and development stage. Stage 3 (production authority): traffic_attached and execution_attached are changed from Literal[False] to True in the boundary module source code, enabling live enforcement.

Currently all boundaries are at Stage 1 or Stage 2. Production authority attachment (Stage 3) is cloud-managed and requires the code-level change described above.

## Gate system: gate1 through gate5b

The shadow/ directory (52 files) contains the staged gate system for progressive authority activation. Each gate validates preconditions before the next level of authority is enabled: gate1 (basic healthcheck, simple assistant text), gate2 (fixture infrastructure), gate3a (recorded replay with input bundles and output reports), gate3b (real-time simulation), gate4 (dry-run shadows + comparison reports), gate5a (memory-free canary testing), gate5b (user-visible routing canary with mocked runner).

Gate5B canary checks are integrated into the transport layer: POST /v1/chat/completions (transport/chat.py) runs Gate5B canary checks before routing to the agent. The canary pattern works with the evidence system: gates check that evidence contracts are passing, that boundary decisions are producing expected results, and that no regression has been introduced. Authority flags in PythonRuntimeAuthorityConfig gate each stage.

## Evidence enforcement configuration

The evidence enforcement boundary (evidence/enforcement_boundary.py) accepts configuration that controls its behavior at each stage. The key configuration fields are: enabled (whether the boundary is active), local_fake_evaluation_enabled (whether to simulate enforcement locally), evidence_block_enabled (whether missing evidence can block operations), and final_answer_blocking_enabled (whether contract verdicts with on_missing=block_final_answer actually prevent output).

All gates are implemented in the codebase. Production authority attachment is managed externally and is not configurable through magi-agent.yaml or environment variables alone.
