"""PR-E4 — keybinding subsystem tests (pure pytest, no Textual App needed).

Covers the schema/grammar, the load->merge->validate pipeline, the pure chord
resolver, and the terminal quirks (alt=meta collapse, escape-sets-meta). The
whole ``cli.keybindings`` package is import-clean (no textual/rich/google-adk),
which is asserted in a fresh-interpreter subprocess (mirrors the engine guard).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from magi_agent.cli.keybindings import (
    Context,
    Keystroke,
    ParsedBinding,
    ResultKind,
    keystroke_from_event,
    load_keybindings,
    parse_chord,
    parse_keystroke,
    resolve,
)
from magi_agent.cli.keybindings.defaults import default_bindings


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _ks(s: str) -> Keystroke:
    return parse_keystroke(s)


# ---------------------------------------------------------------------------
# 1. keystroke / chord grammar
# ---------------------------------------------------------------------------
def test_parse_keystroke_modifiers_and_aliases() -> None:
    ks = parse_keystroke("ctrl+shift+k")
    assert ks.key == "k" and ks.ctrl and ks.shift and not ks.alt and not ks.meta

    assert parse_keystroke("control+k") == parse_keystroke("ctrl+k")
    assert parse_keystroke("opt+x") == parse_keystroke("alt+x")
    assert parse_keystroke("option+x") == parse_keystroke("alt+x")
    assert parse_keystroke("cmd+x").super is True
    assert parse_keystroke("command+x") == parse_keystroke("super+x")
    assert parse_keystroke("win+x") == parse_keystroke("super+x")


def test_parse_keystroke_key_aliases() -> None:
    assert parse_keystroke("esc").key == "escape"
    assert parse_keystroke("return").key == "enter"
    assert parse_keystroke("enter").key == "enter"
    assert parse_keystroke("up").key == "up"


def test_parse_chord_space_is_separator_but_lone_space_is_key() -> None:
    chord = parse_chord("ctrl+x ctrl+k")
    assert len(chord) == 2
    assert chord[0] == parse_keystroke("ctrl+x")
    assert chord[1] == parse_keystroke("ctrl+k")

    # the lone string " " is the space key, not a separator
    space = parse_chord(" ")
    assert len(space) == 1
    assert space[0].key == "space" or space[0].key == " "


# ---------------------------------------------------------------------------
# 2. resolver — last-wins override
# ---------------------------------------------------------------------------
def _b(context: Context, chord_str: str, action: str | None) -> ParsedBinding:
    return ParsedBinding(chord=parse_chord(chord_str), action=action, context=context)


def test_last_wins_override() -> None:
    bindings = [
        _b(Context.CHAT, "ctrl+s", "chat:submit"),
        _b(Context.CHAT, "ctrl+s", "global:quit"),  # later wins
    ]
    res = resolve(_ks("ctrl+s"), [Context.CHAT], bindings, None)
    assert res.kind is ResultKind.MATCH
    assert res.action == "global:quit"


# ---------------------------------------------------------------------------
# 3. null-unbind reveals the shorter binding
# ---------------------------------------------------------------------------
def test_null_unbind_reveals_shorter_binding() -> None:
    bindings = [
        _b(Context.CHAT, "ctrl+x ctrl+k", "chat:cancel"),  # default chord
        _b(Context.CHAT, "ctrl+x", "chat:submit"),  # single key
        _b(Context.CHAT, "ctrl+x ctrl+k", None),  # user unbinds the chord
    ]
    # ctrl+x should now FIRE the single binding (not enter chord-wait)
    res = resolve(_ks("ctrl+x"), [Context.CHAT], bindings, None)
    assert res.kind is ResultKind.MATCH
    assert res.action == "chat:submit"


def test_chord_wait_without_unbind() -> None:
    bindings = [
        _b(Context.CHAT, "ctrl+x ctrl+k", "chat:cancel"),
        _b(Context.CHAT, "ctrl+x", "chat:submit"),
    ]
    # with the chord still bound, ctrl+x must prefer the longer chord
    res = resolve(_ks("ctrl+x"), [Context.CHAT], bindings, None)
    assert res.kind is ResultKind.CHORD_STARTED
    assert res.pending is not None and len(res.pending) == 1


# ---------------------------------------------------------------------------
# 4. chord-prefix resolution
# ---------------------------------------------------------------------------
def test_chord_first_key_then_match() -> None:
    bindings = [_b(Context.CHAT, "ctrl+x ctrl+k", "chat:cancel")]
    r1 = resolve(_ks("ctrl+x"), [Context.CHAT], bindings, None)
    assert r1.kind is ResultKind.CHORD_STARTED
    assert r1.pending is not None
    r2 = resolve(_ks("ctrl+k"), [Context.CHAT], bindings, r1.pending)
    assert r2.kind is ResultKind.MATCH
    assert r2.action == "chat:cancel"


def test_chord_wrong_second_key_cancels() -> None:
    bindings = [_b(Context.CHAT, "ctrl+x ctrl+k", "chat:cancel")]
    r1 = resolve(_ks("ctrl+x"), [Context.CHAT], bindings, None)
    r2 = resolve(_ks("z"), [Context.CHAT], bindings, r1.pending)
    assert r2.kind is ResultKind.CHORD_CANCELLED


def test_chord_esc_mid_chord_cancels() -> None:
    bindings = [_b(Context.CHAT, "ctrl+x ctrl+k", "chat:cancel")]
    r1 = resolve(_ks("ctrl+x"), [Context.CHAT], bindings, None)
    r2 = resolve(_ks("escape"), [Context.CHAT], bindings, r1.pending)
    assert r2.kind is ResultKind.CHORD_CANCELLED


# ---------------------------------------------------------------------------
# 5. alt/meta collapse + escape-sets-meta
# ---------------------------------------------------------------------------
def test_alt_meta_collapse() -> None:
    # an alt+x binding matches a meta+x keystroke
    bindings = [_b(Context.CHAT, "alt+x", "chat:cancel")]
    res = resolve(Keystroke(key="x", meta=True), [Context.CHAT], bindings, None)
    assert res.kind is ResultKind.MATCH

    # and vice versa
    bindings2 = [_b(Context.CHAT, "meta+x", "chat:cancel")]
    res2 = resolve(Keystroke(key="x", alt=True), [Context.CHAT], bindings2, None)
    assert res2.kind is ResultKind.MATCH


def test_super_stays_distinct() -> None:
    bindings = [_b(Context.CHAT, "super+x", "chat:cancel")]
    # an alt+x keystroke must NOT match a super+x binding
    res = resolve(Keystroke(key="x", alt=True), [Context.CHAT], bindings, None)
    assert res.kind is ResultKind.NONE


def test_escape_sets_meta_guard() -> None:
    # a bare escape binding matches an escape keystroke even when meta=true
    bindings = [_b(Context.CHAT, "escape", "chat:cancel")]
    res = resolve(Keystroke(key="escape", meta=True), [Context.CHAT], bindings, None)
    assert res.kind is ResultKind.MATCH
    assert res.action == "chat:cancel"


# ---------------------------------------------------------------------------
# 6. context filtering & priority
# ---------------------------------------------------------------------------
def test_only_active_contexts_resolve() -> None:
    bindings = [_b(Context.SELECT, "ctrl+s", "chat:submit")]
    res = resolve(_ks("ctrl+s"), [Context.CHAT], bindings, None)
    assert res.kind is ResultKind.NONE


def test_global_context_always_checked() -> None:
    bindings = [_b(Context.GLOBAL, "ctrl+q", "global:quit")]
    res = resolve(_ks("ctrl+q"), [Context.GLOBAL], bindings, None)
    assert res.kind is ResultKind.MATCH


# ---------------------------------------------------------------------------
# 7. loader: merge order + validation
# ---------------------------------------------------------------------------
def test_load_defaults_when_no_path() -> None:
    bindings, warnings = load_keybindings(None)
    assert bindings  # non-empty
    assert bindings == default_bindings()
    assert warnings == []


def test_loader_merge_order_user_after_defaults(tmp_path: Path) -> None:
    cfg = {
        "bindings": [
            {"context": "Chat", "bindings": {"ctrl+s": "chat:cancel"}},
        ]
    }
    p = tmp_path / "keybindings.json"
    p.write_text(json.dumps(cfg))
    bindings, warnings = load_keybindings(str(p))
    # defaults come first, user blocks appended last
    n_defaults = len(default_bindings())
    assert bindings[:n_defaults] == default_bindings()
    assert bindings[-1].context is Context.CHAT
    assert bindings[-1].action == "chat:cancel"


def test_loader_missing_file_is_defaults_only(tmp_path: Path) -> None:
    bindings, warnings = load_keybindings(str(tmp_path / "nope.json"))
    assert bindings == default_bindings()
    assert warnings == []


def test_loader_unknown_action_warns(tmp_path: Path) -> None:
    cfg = {"bindings": [{"context": "Chat", "bindings": {"ctrl+s": "chat:bogus"}}]}
    p = tmp_path / "kb.json"
    p.write_text(json.dumps(cfg))
    _, warnings = load_keybindings(str(p))
    assert any("action" in w.message.lower() for w in warnings)


def test_loader_unknown_context_warns(tmp_path: Path) -> None:
    cfg = {"bindings": [{"context": "Nope", "bindings": {"ctrl+s": "chat:submit"}}]}
    p = tmp_path / "kb.json"
    p.write_text(json.dumps(cfg))
    _, warnings = load_keybindings(str(p))
    assert any("context" in w.message.lower() for w in warnings)


def test_loader_command_chat_only_rule(tmp_path: Path) -> None:
    cfg = {
        "bindings": [
            {"context": "Select", "bindings": {"ctrl+s": "command:deploy"}},
        ]
    }
    p = tmp_path / "kb.json"
    p.write_text(json.dumps(cfg))
    _, warnings = load_keybindings(str(p))
    assert any("command" in w.message.lower() for w in warnings)


def test_loader_command_valid_in_chat(tmp_path: Path) -> None:
    cfg = {
        "bindings": [
            {"context": "Chat", "bindings": {"ctrl+s": "command:deploy"}},
        ]
    }
    p = tmp_path / "kb.json"
    p.write_text(json.dumps(cfg))
    bindings, warnings = load_keybindings(str(p))
    assert bindings[-1].action == "command:deploy"
    assert not any("command" in w.message.lower() for w in warnings)


def test_loader_reserved_shortcut_warns(tmp_path: Path) -> None:
    cfg = {"bindings": [{"context": "Chat", "bindings": {"ctrl+c": "chat:cancel"}}]}
    p = tmp_path / "kb.json"
    p.write_text(json.dumps(cfg))
    _, warnings = load_keybindings(str(p))
    assert any("reserved" in w.message.lower() or "rebind" in w.message.lower() for w in warnings)


def test_loader_duplicate_key_in_raw_json_warns(tmp_path: Path) -> None:
    # JSON with a literal duplicate key — json.loads silently drops one, so the
    # detection must scan the raw text.
    raw = (
        '{"bindings":[{"context":"Chat","bindings":'
        '{"ctrl+s":"chat:submit","ctrl+s":"chat:cancel"}}]}'
    )
    p = tmp_path / "kb.json"
    p.write_text(raw)
    _, warnings = load_keybindings(str(p))
    assert any("duplicate" in w.message.lower() for w in warnings)


def test_loader_duplicate_key_with_brace_in_value_warns(tmp_path: Path) -> None:
    # a dup where a string VALUE contains a literal `}` — the old non-greedy
    # `"bindings":\{(.*?)\}` regex stopped at the first `}` and missed this.
    raw = (
        '{"bindings":[{"context":"Chat","bindings":'
        '{"ctrl+s":"a}b","ctrl+s":"chat:cancel"}}]}'
    )
    p = tmp_path / "kb.json"
    p.write_text(raw)
    _, warnings = load_keybindings(str(p))
    assert any("duplicate" in w.message.lower() for w in warnings)


def test_loader_duplicate_key_with_nested_object_value_warns(tmp_path: Path) -> None:
    # a dup that survives a nested-object value (extra `{...}` before the dup).
    raw = (
        '{"$meta":{"x":"y"},"bindings":[{"context":"Chat","bindings":'
        '{"ctrl+s":"chat:submit","ctrl+s":"chat:cancel"}}]}'
    )
    p = tmp_path / "kb.json"
    p.write_text(raw)
    _, warnings = load_keybindings(str(p))
    assert any("duplicate" in w.message.lower() for w in warnings)


def test_loader_brace_in_string_value_is_not_duplicate(tmp_path: Path) -> None:
    # a `}` inside a string value must NOT be mistaken for the end of the object,
    # and there is NO actual duplicate here.
    raw = (
        '{"bindings":[{"context":"Chat","bindings":'
        '{"ctrl+s":"a}b","ctrl+x":"c{d}e"}}]}'
    )
    p = tmp_path / "kb.json"
    p.write_text(raw)
    _, warnings = load_keybindings(str(p))
    assert not any("duplicate" in w.message.lower() for w in warnings)


def test_loader_alt_meta_normalized_duplicate_warns(tmp_path: Path) -> None:
    # alt+x and meta+x collapse to the same resolver identity (last-wins, silent
    # collision) — the per-context normalized dup check must flag this.
    cfg = {
        "bindings": [
            {
                "context": "Chat",
                "bindings": {"alt+x": "chat:submit", "meta+x": "chat:cancel"},
            }
        ]
    }
    p = tmp_path / "kb.json"
    p.write_text(json.dumps(cfg))
    _, warnings = load_keybindings(str(p))
    assert any("duplicate" in w.message.lower() for w in warnings)


def test_loader_unparseable_keystroke_warns(tmp_path: Path) -> None:
    cfg = {"bindings": [{"context": "Chat", "bindings": {"ctrl+": "chat:submit"}}]}
    p = tmp_path / "kb.json"
    p.write_text(json.dumps(cfg))
    _, warnings = load_keybindings(str(p))
    assert any("keystroke" in w.message.lower() or "parse" in w.message.lower() for w in warnings)


def test_loader_malformed_json_warns_not_raises(tmp_path: Path) -> None:
    p = tmp_path / "kb.json"
    p.write_text("{ this is not json ")
    bindings, warnings = load_keybindings(str(p))
    # falls back to defaults, emits a warning, never raises
    assert bindings == default_bindings()
    assert warnings


# ---------------------------------------------------------------------------
# 8. normalization (modifier order-insensitive within a chord step)
# ---------------------------------------------------------------------------
def test_normalization_modifier_order_insensitive() -> None:
    assert parse_keystroke("ctrl+shift+k") == parse_keystroke("shift+ctrl+k")
    bindings = [_b(Context.CHAT, "shift+ctrl+k", "chat:cancel")]
    res = resolve(_ks("ctrl+shift+k"), [Context.CHAT], bindings, None)
    assert res.kind is ResultKind.MATCH


# ---------------------------------------------------------------------------
# 9. duck-typed event adapter (no textual import)
# ---------------------------------------------------------------------------
class _FakeEvent:
    def __init__(self, key: str, character: str | None = None) -> None:
        self.key = key
        self.character = character
        self.name = key.replace("+", "_")


def test_keystroke_from_event_ctrl() -> None:
    ks = keystroke_from_event(_FakeEvent("ctrl+s"))
    assert ks is not None and ks.ctrl and ks.key == "s"


def test_keystroke_from_event_plain_char() -> None:
    ks = keystroke_from_event(_FakeEvent("x", character="x"))
    assert ks is not None and ks.key == "x" and not ks.ctrl


def test_keystroke_from_event_escape() -> None:
    ks = keystroke_from_event(_FakeEvent("escape"))
    assert ks is not None and ks.key == "escape"


# ---------------------------------------------------------------------------
# 10. import-cleanliness — no textual/rich/google-adk anywhere in the package
# ---------------------------------------------------------------------------
def test_keybindings_package_import_clean_in_fresh_interpreter() -> None:
    import subprocess

    code = (
        "import magi_agent.cli.keybindings as kb;"
        "import magi_agent.cli.keybindings.schema;"
        "import magi_agent.cli.keybindings.resolver;"
        "import magi_agent.cli.keybindings.defaults;"
        "import magi_agent.cli.keybindings.loader;"
        "import sys;"
        "print(any(m=='textual' or m.startswith('textual.') for m in sys.modules),"
        "any(m=='rich' or m.startswith('rich.') for m in sys.modules),"
        "any('google.adk' in m for m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False False False", result.stdout


def test_default_bindings_contain_a_chord_and_reserved_keys() -> None:
    blocks = default_bindings()
    assert any(len(b.chord) > 1 for b in blocks), "expected at least one chord default"
    # reserved keys present-but-special
    chord_strs = {" ".join(_chord_str(b.chord)) for b in blocks}
    assert "ctrl+c" in chord_strs
    assert "ctrl+d" in chord_strs


def _chord_str(chord: tuple[Keystroke, ...]) -> list[str]:
    out: list[str] = []
    for k in chord:
        mods = []
        if k.ctrl:
            mods.append("ctrl")
        if k.alt:
            mods.append("alt")
        if k.shift:
            mods.append("shift")
        if k.meta:
            mods.append("meta")
        if k.super:
            mods.append("super")
        out.append("+".join(mods + [k.key]) if mods else k.key)
    return out
