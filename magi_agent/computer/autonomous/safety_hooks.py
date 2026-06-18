from __future__ import annotations

# Read-only actions can never mutate the machine, so they never pause.
_NON_MUTATING_ACTIONS = frozenset({"capture", "list_apps", "wait", "done"})

_SENSITIVE_MARKERS = (
    "axsecuretextfield",
    "password",
    "passcode",
    "trash",
    "delete",
    "erase",
    "format disk",
    "payment",
    "checkout",
    "purchase",
    "buy now",
    "card number",
    "security & privacy",
    "system settings",
    "system preferences",
    "keychain",
    "terminal",
    "sudo",
)


def is_sensitive_action(action: str, target: str | None = None) -> bool:
    """True if this action must pause for explicit human re-approval.

    Mutating actions whose target touches a secure field, a destructive op,
    payment, system settings, or terminal execution are sensitive. Read-only
    actions (capture/list_apps/wait/done) are never sensitive.
    """
    if action.casefold() in _NON_MUTATING_ACTIONS:
        return False
    if not target:
        return False
    lowered = target.casefold()
    return any(marker in lowered for marker in _SENSITIVE_MARKERS)
