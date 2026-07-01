# Modes

A **mode** is a saved *agent posture*: a reusable bundle of instructions and
tool scope that you switch on for a turn. Modes let you keep one bot but give it
several deliberate stances — a read-only "Review" posture, a focused "Writing"
posture, a "Careful coding" posture — without editing configuration each time.

A mode is an explicit, user-selected choice. Nothing classifies your message and
picks a mode for you: you author modes yourself and select one in the chat
composer (or leave the default). A bot with no modes behaves exactly as it did
before you created any.

## What a mode contains

| Field | Effect |
|-------|--------|
| **System prompt** | Soft guidance injected into the assembled system prompt for the turn. The model is asked to follow it; it is not a hard rule. |
| **Tool delta** | A `exclude` / `include` delta from the bot's default toolset (not a snapshot — tools you install later still appear automatically). |
| **Scoped policies** | Ids of user-authored policies force-activated only while this mode is active (additive — tightens the turn, never loosens; see below). |

A mode is a **delta**, not a full snapshot. You only record the deliberate
differences from the bot default, so the mode keeps working as the underlying
tool set evolves.

## Authoring modes

Open the dashboard, go to **Customize → Modes**, and use the panel to:

- **Create** a mode: give it a display name, an optional system prompt, and
  optional tool `exclude` / `include` lists.
- **Edit** or **delete** an existing mode.
- **Set the active mode**: pick the sticky default the composer starts on, or
  choose **Default** to clear it.

Modes are stored in your `customize.json` under `agent_modes`, with the sticky
selection under `active_agent_mode`.

## Selecting a mode in chat

The chat composer shows a **mode selector** whenever the bot has at least one
mode. Pick a mode to apply it to the messages you send; pick **Default** to send
with no mode. The composer starts on the sticky active mode you set in Customize.

Under the hood the composer sends the selected mode id as an `agentMode` field
on the chat request (the same shape as the reasoning-effort control). A mode
selected for the turn takes precedence over the stored sticky default, and an
empty selection sends no field at all.

## Tool delta

### Exclude — narrow the toolset

`exclude` turns a default-on tool **off** for the turn. This is inherently safe:
a mode can only remove tools, never add capability. It is the mechanism behind a
read-only posture — for example a "Review" mode that excludes the editing and
command tools so the agent can read and reason but not change anything.

### Include — re-enable a default-off tool

`include` turns a default-**off** tool back **on** for the turn, subject to a
property-based hard-safety cap. A mode may re-enable a tool only when it is:

- registered with a working handler and available in the current runtime mode,
- not marked `dangerous`,
- in an allowed permission class (`read` or `write`), and
- in an allowed side-effect class (`none` or `local_workspace`).

Anything outside those allowlists is refused no matter what a mode declares:
shell/command execution, code execution, computer control, outbound network
access, process spawning, external side effects, and tool-management tools all
stay off. The allowlists fail closed, so a tool carrying a class introduced
later is refused until the cap is updated.

`exclude` wins over `include` for the same tool name. The cap only governs what
the model can *see and call*; the permission approval step still applies to any
tool at call time, so this is a defense-in-depth layer rather than the only
guard.

## Scoped policies

`scoped_policy_ids` lets a mode force-activate a user-authored policy **only
while that mode is active** — even a policy that is otherwise globally off. Ids
use the dashboard's prefixed form:

- `custom_rule:<id>` — a custom rule. A `deterministic_ref` rule adds its
  evidence ref to the pre-final gate; a `tool_perm` rule is enforced at
  tool-call time.
- `dashboard_check:<id>` — a dashboard check is emitted (block or audit) at
  tool-call time.

Scoping is **additive and cannot loosen**: a mode can require *more* for its own
turns, never less — it cannot disable a globally-active policy, and a scoped rule
can never shadow an enabled one. It also respects the same enable flags as the
policies themselves (a scoped id never activates a path an operator turned off),
and a scoped id only ever activates a policy that actually exists.

Not yet applied: `seam_spec:` and bare `verifier:` refs (their runtime path is
not wired), and the other custom-rule kinds (`llm_criterion`, `shacl_constraint`,
`prompt_injection`, `output_rewrite`, `shell_*`, `capability_scope`), which fire
at points without a per-turn activation seam today. A scoped id naming one of
those is stored but has no effect.

Policies you author in **Customize → Policies** without scoping still apply
globally regardless of the active mode.

## No enable flag

Modes have no on/off environment flag. The feature is gated by the presence of a
mode: with no modes authored and none active, prompt assembly and the toolset are
byte-identical to a bot without the feature. You opt in simply by creating a mode
and selecting it.

## Relationship to other concepts

- A mode is distinct from a **verification mode** (`deterministic` / `audit`),
  which is a per-check enforcement setting under Policies. Modes are postures;
  verification modes are how a specific check runs.
- Modes compose with **packs** and **policies**: a mode changes the posture and
  visible toolset for a turn, while packs and policies define the capabilities
  and rules that are available to be scoped.

See also: [Customization](customization.md), [Tools](tools.md),
[Configuration reference](config-reference.md).
