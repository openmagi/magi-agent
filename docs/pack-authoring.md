# Write Your First Pack

Magi's runtime is neutral: every primitive seam — tool, callback, validator,
harness, control-plane policy, evidence producer, recipe, connector — is
authored through the **same disk-pack mechanism first-party uses**. First-party
ships as bundled packs in the same format and loader; your pack can add a new
primitive, override a first-party ref, or remove a first-party pack entirely.

## The fastest path

    magi pack new validator my-check

scaffolds `<cwd>/.magi/packs/my_check/` with a validated `pack.toml`, an impl
stub receiving only its typed context, and a pytest smoke test. Edit the impl,
run the smoke test, done — the runtime discovers `<cwd>/.magi/packs/` and
`~/.magi/packs/` automatically, and impl imports need **zero env setup** (the
loader auto-resolves your pack's modules; no PYTHONPATH).

`magi pack new <type> <name>` supports all 8 types: `tool`, `callback`,
`validator`, `harness`, `control_plane`, `evidence_producer`, `recipe`,
`connector`.

## By hand: a control-plane pack in three files

This is the exact shape the runtime's own no-privilege keystone test loads in
parallel with first-party. Drop it at `~/.magi/packs/user_cp/` (or
`<cwd>/.magi/packs/user_cp/`):

`pack.toml`:

    packId = "user.control-plane-extra"
    displayName = "user cp extra"
    version = "0.0.1"

    [[provides]]
    type = "control_plane"
    ref = "control_plane:user-extra@1"
    impl = "user_cp.impl:provide"
    gatePosition = "after"

`__init__.py` (empty), and `impl.py`:

    from magi_agent.adk_bridge.control_plane import BaseLoopControl

    class UserParallelControl(BaseLoopControl):
        name = "user.parallel.control"

        async def on_before_model(self, *, callback_context, llm_request):
            return None

    def provide(ctx):
        ctx.register(UserParallelControl())

That's it. On the next run your control registers alongside the bundled
first-party controls — loaded by the identical loader, ordered by the same
manifest `priority`, receiving the identical `ControlPlaneProvideContext`.

## Override and remove

- **Override:** declare the same `(type, ref)` as a first-party entry — the
  last pack in resolved order wins, and your pack dirs (`~/.magi/packs/`,
  `<cwd>/.magi/packs/`) load after the bundled first-party base, so your impl
  replaces first-party's with no special-casing.
- **Remove:** disable any pack (first-party included) in `~/.magi/config.toml`:

      [packs]
      disable = ["openmagi.source-opened"]

## Capability parity

Your impl receives the same narrow typed context first-party receives — see
the Typed-Context API Reference for what each type can do (its capability
ceiling) and the Pack Manifest Reference for the full `pack.toml` schema. For a
working per-type starting template, scaffold one (`magi pack new <type>
<name>`): each generated stub is a copy-shape of the matching bundled
first-party impl, and its smoke test loads the pack through the real loader.
