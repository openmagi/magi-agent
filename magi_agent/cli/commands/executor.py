"""Default ``CommandExecutor`` for the Magi TUI (Stream D/F, PR2.2).

Maps a looked-up ``Command`` onto app-facing effects WITHOUT a second engine
loop. Imports only ``contracts`` (no textual / no model deps), so importing it
is cheap and side-effect-free.

KIND routing (the ``Command`` union discriminates by subclass, not a string
field):

* :class:`PromptCommand` -> expand ``build_prompt`` into a prompt string and
  re-enter the ONE turn loop via ``ctx.app.start_turn``. No new loop is created.
* :class:`LocalCommand` -> run ``call`` and apply the ``LocalResult``: ``Text``
  commits to the transcript, ``Compact`` requests compaction, ``Skip`` is a
  no-op.
* :class:`WidgetCommand` -> run ``call`` with a no-op ``on_done``; the widget
  drives its opener off ``ctx.app`` (e.g. ``ctx.app.open_dialog(...)``). Full
  ``on_done`` plumbing is PR2.3/2.4 territory.
"""

from __future__ import annotations

from magi_agent.cli.contracts import (
    Command,
    CommandContext,
    CommandExecutor,
    Compact,
    ContentBlock,
    LocalCommand,
    PromptCommand,
    Skip,
    Text,
    WidgetCommand,
    WidgetDone,
)

__all__ = ["DefaultCommandExecutor"]


def _blocks_to_text(blocks: list[ContentBlock]) -> str:
    """Flatten prompt content blocks into the turn prompt string."""

    return "\n".join(
        b.text for b in blocks if isinstance(b, ContentBlock) and b.text
    )


class DefaultCommandExecutor(CommandExecutor):
    """Concrete executor wiring builtins/bundled/markdown commands to the app."""

    async def run(self, command: Command, args: str, ctx: CommandContext) -> None:
        app = ctx.app
        if isinstance(command, PromptCommand):
            blocks = await command.build_prompt(args, ctx)
            prompt = _blocks_to_text(blocks)
            if prompt and app is not None and hasattr(app, "start_turn"):
                # Re-enter the ONE turn loop. No new loop is created here.
                app.start_turn(prompt)
            return

        if isinstance(command, LocalCommand):
            result = await command.call(args, ctx)
            self._apply_local(result, app)
            return

        if isinstance(command, WidgetCommand):
            await self._run_widget(command, args, ctx)
            return

    @staticmethod
    def _apply_local(result: object, app: object | None) -> None:
        if app is None:
            return
        if isinstance(result, Text):
            commit = getattr(app, "commit_text", None)
            if callable(commit):
                commit(result.text)
        elif isinstance(result, Compact):
            request = getattr(app, "request_compact", None)
            if callable(request):
                request()
        elif isinstance(result, Skip):
            return

    @staticmethod
    async def _run_widget(
        command: WidgetCommand, args: str, ctx: CommandContext
    ) -> None:
        # Widgets open a dialog/picker. The widget's ``call`` drives the opener
        # off ``ctx.app`` (e.g. ``ctx.app.open_dialog(...)``). We pass a no-op
        # ``on_done`` here; the dialog resolves its own effect via the app and
        # may submit a follow-up turn through ``ctx.app.start_turn`` — still the
        # single turn loop. Full on_done plumbing is PR2.3/2.4 territory.
        def _on_done(result: object, **_kw: object) -> None:
            _ = result

        done: WidgetDone = _on_done  # type: ignore[assignment]
        await command.call(done, ctx, args)
