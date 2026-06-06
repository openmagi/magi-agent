# Plugin Manifest

Package and distribute runtime extensions as plugins with a declared manifest.

Plugin manifests declare what surfaces, hooks, tools, and evidence types a plugin provides.

## RecipePackManifest

RecipePackManifest (recipes/compiler.py) is the primary manifest type for packaging task-specific runtime behavior. It is a frozen Pydantic model that declares what a recipe pack provides and how it integrates with the harness engine.

- pack_id (packId): unique identifier for the recipe pack.
- display_name (displayName): human-readable name shown in the recipe catalog.
- description: what the recipe pack does.
- hard_safety (hardSafety): whether the pack enforces hard safety invariants that cannot be opted out of. Default False.
- opt_out_allowed (optOutAllowed): whether users can disable this pack. Default True.
- customizable: whether the pack's parameters can be overridden by configuration. Default True.
- task_profile_selectors (taskProfileSelectors): task profile patterns that trigger this pack.
- depends_on_pack_ids (dependsOnPackIds): other packs that must be loaded first.
- evidence_refs (evidenceRefs): evidence contract IDs that this pack contributes.
- tool_refs (toolRefs), callback_refs (callbackRefs), validator_refs (validatorRefs): references to tools, callbacks, and validators contributed by this pack.

## HookManifest

HookManifest (hooks/manifest.py) defines how a single hook is registered and configured. Hooks are the lifecycle observation and intervention mechanism. See [Hook Points](/docs/hook-points) for the 15 available hook points.

- name: unique hook identifier.
- point: HookPoint enum value (e.g., beforeToolUse, afterCommit, onTaskCheckpoint).
- description: human-readable purpose of this hook.
- source: ToolSource indicating where the hook implementation lives.
- priority: execution order within a hook point. Default 100, lower runs first.
- blocking: whether the hook can block the operation. Default True.
- fail_open (failOpen): whether hook failure allows the operation to continue. Default False.
- timeout_ms (timeoutMs): maximum execution time in milliseconds. Default 5000.
- enabled: whether the hook is active. Default True.
- security_critical (securityCritical): marks hooks that enforce security invariants. Default False.
- if_condition (if): optional condition expression for conditional execution.
- scope: HookScope for scoping to specific contexts.
- opt_out (optOut): whether the hook can be opted out of. Default True.

## EvidenceContract as evidence manifest

EvidenceContract (evidence/types.py) serves as the manifest for evidence requirements. It declares what evidence must be present before the agent can proceed past a checkpoint. See [Evidence Contracts](/docs/evidence-contracts) for authoring details.

Key fields: id (contract identifier), description, triggers (afterToolUse or beforeCommit), requirements (list of EvidenceRequirement), on_missing (audit or block_final_answer), retry_message (message shown when evidence is missing), and scope (EvidenceContractScopeMetadata for agent role and spawn depth filtering).

## PluginStatus and ResolvedPluginState

PluginStatus (plugins/manager.py) tracks the state of each plugin at runtime. ResolvedPluginState aggregates all plugin states. Native plugins are defined in plugins/native_catalog.py. Status: metadata only, no live plugins active.

- PluginStatus fields: plugin_id, kind, version, installed, enabled, tools, hooks, harness_rules.
- traffic_attached: always False. No live traffic routes through plugins.
- execution_attached: always False. No live execution happens through plugins.
- ResolvedPluginState: aggregated state of all resolved plugins, created by OpenMagiRuntime.__init__().

## PluginManifest

PluginManifest (plugins/manifest.py) is the top-level manifest for bundling tools, hooks, harness rules, and configuration into a distributable plugin. It includes security metadata for sandboxing and trust classification.

- plugin_id (id): dotted namespace identifier (e.g., org.example.my-plugin). Validated against a strict regex pattern.
- kind: PluginKind enum (core, native, or custom).
- version: semantic version string.
- permissions: tuple of PermissionClass values (read, write, execute, net, meta).
- tools: tuple of PluginToolRef with name and entrypoint (module:callable format).
- hooks: tuple of PluginHookRef with name, point, and optional entrypoint.
- trust_level (trustLevel): PluginTrustLevel for sandbox policy enforcement. Default untrusted.
- supply_chain_digest (supplyChainDigest): optional content hash for supply chain verification.
- sandbox: optional PluginSandboxPolicy for execution isolation.

## Manifest loading and validation

All manifest types use Pydantic's strict validation with frozen=True models. Invalid manifests are rejected at parse time with detailed validation errors. The HarnessEngine loads hook manifests and evidence contract scopes at initialization and resolves them against a HarnessResolutionRequest that specifies the agent role, spawn depth, and opt-out preferences.

Manifest loading is currently internal to the runtime. An external plugin registry for discovering and installing third-party plugins is planned but not yet available. Plugins are loaded from the local filesystem or bundled with the runtime image. Native plugins from native_catalog.py are the only active plugin source.
