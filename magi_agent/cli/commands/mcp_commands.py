"""MCP prompts → CLI slash-commands bridge (Stream D, P2).

Exposes ``mcp_prompt_commands(adapter, provider, server_refs, security_manifests)``
which converts a local-fake MCP server's ``prompts/list`` projection into
``McpPromptCommand`` objects for the CLI command registry. These commands live in
the discovery **``plugin`` tier (tier 5)** and are ONLY produced when a caller
explicitly injects an MCP provider — there is NO live MCP connection by default
(default-off).

This module is the discovery-side mirror of ``magi_agent.plugins.mcp_adapter``'s
prompt projection path. The adapter remains the single security boundary: it
gates (disabled → manifest → local-fake-required → untrusted) and redacts both
``prompts/list`` descriptors AND the ``prompts/get`` body. This module merely
turns its ``McpPromptListDecision`` (status == "ok") into commands whose
resolvers route ``prompts/get`` through ``McpAdapter.resolve_prompt`` — so the
model-facing prompt text is REDACTED at the same seam the tool path uses, never
read raw from the provider here.

Sync + default-off invariants
-----------------------------
- Everything here is SYNC. The injected ``provider.get_prompt`` is sync; the
  ``McpPromptCommand.resolver`` is a sync callable. ``build_prompt`` is an
  ``async`` coroutine only to satisfy the ``PromptCommand`` contract; it does no
  awaiting / no live calls and never opens a socket.
- ``mcp_prompt_commands`` returns ``[]`` whenever the adapter is disabled,
  blocked, or no provider is supplied. It never itself connects to anything.

Live wiring (a real MCP client behind ``provider``) is a DOCUMENTED FUTURE SEAM;
nothing here imports an MCP client, opens sockets, performs network egress, or
flips an authority/``live_*`` flag.

Argument convention
-------------------
Prompt arguments map onto ``$1``..``$N`` exactly like markdown commands:
``$1`` → the first positional token, ``$2`` → the second, etc. ``hints`` is
computed from the descriptor's argument COUNT (``["$1", .. "$N"]``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from magi_agent.cli.contracts import (
    Command,
    CommandContext,
    CommandSurface,
    ContentBlock,
    PromptCommand,
)
from magi_agent.plugins.mcp_adapter import (
    McpAdapter,
    McpProviderPort,
    McpPromptDescriptor,
    McpServerSecurityManifest,
)

__all__ = [
    "MCP_SURFACE",
    "McpPromptCommand",
    "mcp_prompt_commands",
]

# MCP prompt commands expand into model prompt content; usable in both surfaces.
MCP_SURFACE = CommandSurface(tui=True, headless=True)

# A sync resolver maps an argument mapping (argName → value) to the template text.
PromptResolver = Callable[[Mapping[str, str]], str]


@dataclass
class McpPromptCommand(PromptCommand):
    """A ``PromptCommand`` backed by an MCP ``prompts/get`` resolution.

    Fields carry the projected, redaction-safe descriptor data
    (``name``/``description``/argument names) plus a SYNC ``resolver`` callable
    that, given a mapping of argument name → value, returns the prompt template
    text. ``source = "mcp"`` marks the origin; ``hints`` is computed from the
    argument count (``$1``..``$N``).

    ``build_prompt`` splits ``args`` into positional tokens, maps them onto the
    prompt's argument names (``$1`` → first arg name, etc.), calls the sync
    ``resolver`` to obtain the template text, and returns it as one
    ``ContentBlock``. It performs no live/async work.
    """

    description: str = ""
    argument_names: tuple[str, ...] = ()
    resolver: PromptResolver | None = None
    source: str = "mcp"
    hints: list[str] = field(default_factory=list)

    async def build_prompt(  # type: ignore[override]
        self, args: object, ctx: CommandContext
    ) -> list[ContentBlock]:
        _ = ctx
        mapping = self._argument_mapping(args)
        text = "" if self.resolver is None else self.resolver(mapping)
        return [ContentBlock(type="text", text=text)]

    def _argument_mapping(self, args: object) -> dict[str, str]:
        """Map positional tokens from ``args`` onto the prompt's argument names.

        ``$1`` → first token → ``argument_names[0]``, and so on. Missing
        positionals map to ``""`` (mirrors markdown ``$N`` substitution). Extra
        tokens beyond the named arguments are ignored.
        """
        arg_str = "" if args is None else str(args)
        tokens = arg_str.split()
        mapping: dict[str, str] = {}
        for idx, name in enumerate(self.argument_names):
            mapping[name] = tokens[idx] if idx < len(tokens) else ""
        return mapping


def _hints_for_count(count: int) -> list[str]:
    """Compute ``["$1", .. "$N"]`` for an argument ``count`` (reuses ``$N`` form)."""
    return [f"${n}" for n in range(1, count + 1)]


def _make_resolver(
    adapter: McpAdapter,
    provider: McpProviderPort,
    server_ref: str,
    descriptor: McpPromptDescriptor,
    security_manifest: McpServerSecurityManifest | Mapping[str, object] | None,
) -> PromptResolver:
    """Build a sync resolver routing through the gated, REDACTING adapter seam.

    The closure derives the un-namespaced prompt name from the projected
    descriptor (the namespaced form is CLI-facing only) and asks the
    ``McpAdapter`` — NOT the raw provider — to resolve the prompt. The adapter
    re-applies the same gates (disabled → manifest → local-fake-required →
    untrusted → provider-error) and routes the prompt body through ``_safe_text``
    redaction (mirroring the tool path) before handing back text. A
    blocked/error/disabled decision yields an empty/safe string here; this
    resolver never returns raw provider text and never raises.
    """
    # Recover the leaf prompt segment from the namespaced descriptor name
    # ("mcp.<server>.<prompt>" → "<prompt>"). The provider keys prompts by their
    # own (already projected/safe) leaf name.
    #
    # WARNING (live wiring): ``descriptor.name`` is the PROJECTED-SAFE name —
    # ``_safe_tool_segment`` lowercases it and digests any private text, so this
    # recovered leaf is scrubbed/lowercased, not the server's original prompt
    # key. A real (live) MCP client must key ``prompts/get`` by the
    # projected-safe leaf (or carry the original name out-of-band on the
    # descriptor); using a private/case-sensitive original name here would
    # re-introduce the very leak the projection closes.
    prompt_name = descriptor.name.rsplit(".", 1)[-1]

    def _resolve(arguments: Mapping[str, str]) -> str:
        decision = adapter.resolve_prompt(
            server_ref,
            prompt_name,
            dict(arguments),
            provider=provider,
            security_manifest=security_manifest,
        )
        if decision.status != "ok":
            return ""
        return decision.text

    return _resolve


def mcp_prompt_commands(
    adapter: McpAdapter,
    provider: McpProviderPort | None,
    server_refs: Sequence[str],
    security_manifests: Mapping[str, McpServerSecurityManifest | Mapping[str, object]] | None = None,
) -> list[Command]:
    """Project local-fake MCP prompts into ``McpPromptCommand`` objects.

    For each ``server_ref``, calls ``adapter.list_prompts(...)`` and — ONLY when
    the resulting decision status is ``"ok"`` — builds one ``McpPromptCommand``
    per descriptor whose resolver closes over ``provider.get_prompt(server_ref,
    name, ...)``.

    Returns ``[]`` (DEFAULT-OFF) when the adapter is disabled/blocked or when no
    provider is supplied. This function never connects to anything: the adapter
    is the gate and the provider is a caller-injected local-fake.
    """
    if provider is None:
        return []
    manifests = security_manifests or {}
    commands: list[Command] = []
    for server_ref in server_refs:
        security_manifest = manifests.get(server_ref)
        decision = adapter.list_prompts(
            server_ref,
            provider=provider,
            security_manifest=security_manifest,
        )
        if decision.status != "ok":
            continue
        for descriptor in decision.descriptors:
            commands.append(
                McpPromptCommand(
                    name=descriptor.name,
                    surface=MCP_SURFACE,
                    description=descriptor.description or "",
                    argument_names=descriptor.arguments,
                    resolver=_make_resolver(
                        adapter, provider, server_ref, descriptor, security_manifest
                    ),
                    source="mcp",
                    hints=_hints_for_count(len(descriptor.arguments)),
                )
            )
    return commands
