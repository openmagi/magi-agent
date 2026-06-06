# CLI

Magi Agent installs two commands:

```bash
magi
magi-agent
```

`magi` is the terminal work interface. `magi-agent` manages the local runtime
server and package-level commands.

## Commands

```bash
magi --help
magi -p "Plan a coding fix"
magi --output text "Summarize this repository"
magi --mode plan "Review the docs and propose edits"
magi --mode act "Apply the approved edits"
```

```bash
magi-agent --help
magi-agent serve --port 8080
```

## Interactive use

Run:

```bash
magi
```

The interactive interface keeps a local session and streams public runtime
events when available.

## One-shot use

```bash
magi -p "Inspect this repository and list the runnable surfaces"
cat notes.md | magi --output text "Extract decisions and open questions"
```

Use one-shot mode for scripts, checks, and quick operator tasks.

## Plan and act modes

Use plan mode when you want the agent to inspect and propose before writing:

```bash
magi --mode plan "Find the likely cause of this failing test"
```

Use act mode only when writes and tool execution are intended:

```bash
magi --mode act "Implement the approved fix and run focused tests"
```

