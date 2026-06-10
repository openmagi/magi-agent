"""Mode-derived permission scope resolution (cluster 09 PR1).

Historically the CLI tool runtime stamped a hardcoded
``permission_scope={"mode": "selected_full_toolhost", "source":
"selected_full_toolhost"}`` onto *every* :class:`~magi_agent.tools.context.ToolContext`.
That unconditional stamp made
:func:`magi_agent.tools.permission.selected_full_toolhost_preapproved` (and the
six matching branches in ``tools/safety.py``) preapprove all write / execute /
net / dangerous / mutating tools to ``allow`` once ``securityPrecheck`` passed —
so the ``RuntimePermissionArbiter`` "ask" branch was effectively unreachable on
the local CLI. The runtime behaved like a silent ``bypassPermissions`` without
surfacing that fact as a mode.

``PermissionScopeResolver`` replaces that stamp with a real, mode-derived scope
so the policy / hard-safety layering is restored:

- ``default`` / ``smartApprove`` -> empty preapproval scope (the arbiter ``ask``
  branch can actually be reached).
- ``acceptEdits``                -> the legacy ``selected_full_toolhost``
  preapproval scope, but ONLY for edit-class tools; everything else gets the
  empty scope so mutating/dangerous non-edit tools still ``ask``.
- ``bypassPermissions``          -> a ``bypass`` scope (recognised by
  ``tools/safety.py`` — hard-safety deny on secret/sealed/workspace-escape is
  still enforced).

This module deliberately does NOT touch the preapproval *consumers*
(``permission.py`` / ``safety.py``); reshaping those six branches is PR3's
vocabulary-unification scope. PR1 only changes which scope is *injected*.
"""

from __future__ import annotations

from magi_agent.tools.manifest import ToolManifest

__all__ = ["PermissionScopeResolver", "EDIT_CLASS_TOOLS"]


# Edit-class tool names that ``acceptEdits`` preapproves. Mirrors
# ``magi_agent.cli.permissions.EDIT_CLASS_TOOLS`` (Layer-A gate) so the
# preapproval scope (Layer-B) agrees on what "an edit" is.
EDIT_CLASS_TOOLS: frozenset[str] = frozenset(
    {"FileEdit", "FileWrite", "Edit", "Write", "ApplyPatch", "PatchApply"}
)


# The legacy preapproval scope still consumed by
# ``selected_full_toolhost_preapproved`` and the ``safety.py`` branches.
_SELECTED_FULL_TOOLHOST_SCOPE: dict[str, object] = {
    "mode": "selected_full_toolhost",
    "source": "selected_full_toolhost",
}

# Bypass scope recognised by ``tools/safety.py`` (``_scope_mode`` -> "bypass").
_BYPASS_SCOPE: dict[str, object] = {"mode": "bypass", "source": "bypass"}


def _normalize_mode(mode: object) -> str:
    if not isinstance(mode, str):
        return "default"
    return mode.strip()


class PermissionScopeResolver:
    """Derive a ``permission_scope`` dict from the permission mode + manifest.

    The resolver is stateless and side-effect free; one shared instance is safe
    to reuse across tool contexts.
    """

    def resolve(
        self,
        *,
        permission_mode: object,
        manifest: ToolManifest,
        channel: str | None = None,
    ) -> dict[str, object]:
        """Return the ``permission_scope`` dict for a tool call.

        Parameters
        ----------
        permission_mode:
            ``"default"`` | ``"acceptEdits"`` | ``"bypassPermissions"`` |
            ``"smartApprove"``. Unknown / non-string values collapse to
            ``"default"`` (the strict, no-preapproval scope).
        manifest:
            The tool's manifest; used by ``acceptEdits`` to decide whether the
            tool is edit-class.
        channel:
            Accepted for forward-compatibility (per-channel policy) but not yet
            consumed.
        """

        del channel  # reserved; see docstring.
        mode = _normalize_mode(permission_mode)

        if mode == "bypassPermissions":
            return dict(_BYPASS_SCOPE)

        if mode == "acceptEdits" and manifest.name in EDIT_CLASS_TOOLS:
            return dict(_SELECTED_FULL_TOOLHOST_SCOPE)

        # default / smartApprove / unknown / acceptEdits-non-edit:
        # no preapproval scope -> the arbiter "ask" branch can reach.
        return {"mode": "default", "source": "builtin"}
