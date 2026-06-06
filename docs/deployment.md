# Deployment

Deploy Magi Agent with default-off authority and explicit rollout gates.

Deployment docs should emphasize local/source inspection, self-host operation, optional managed hosting, and the fact that live authority must be enabled through explicit rollout gates.

## Self-host operation

Self-host deployments should make provider credentials, tool credentials, storage, runtime routes, and public projections explicit. Keep mutation surfaces least-privilege and require approval gates for external side effects.

Open Magi Cloud remains optional managed hosting for teams that do not want to operate the runtime themselves.

## Default-off rollout posture

New runtime authority should start default-off. Enable it only after contract tests, replay, shadow/canary evidence, security review, and rollback plans are in place.

The Python ADK migration follows this posture: documentation can describe the contract while live authority remains gated where the implementation status says default-off.
