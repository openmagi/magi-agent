# magi gateway

Type: Reference — the always-on gateway daemon command family.

The `magi gateway` sub-commands manage an optional always-on daemon that runs
cron jobs and live channel watchers. The daemon is **default OFF**: nothing
runs in the background until you set `MAGI_GATEWAY_DAEMON_ENABLED=1`, and each
individual watcher still respects its own gate (for example the cron watcher
needs `MAGI_SCHEDULER_EXECUTOR_ENABLED`).

```sh
magi gateway status
magi gateway start
magi gateway install --target-path ./magi-gateway.service
magi gateway uninstall --target-path ./magi-gateway.service
```

## `magi gateway status`

Prints whether the daemon gate is set. It has no side effects.

```sh
magi gateway status
# -> gateway daemon: disabled — set MAGI_GATEWAY_DAEMON_ENABLED=1 to enable ...
```

When `MAGI_GATEWAY_DAEMON_ENABLED` is set the line reports `enabled` and notes
that each watcher still has its own gate.

## `magi gateway start`

Runs the daemon, gated on `MAGI_GATEWAY_DAEMON_ENABLED`.

- **Gate OFF** — prints the disabled status and exits without starting anything.
- **Gate ON (default)** — supervises the first-party watcher set via the
  internal gateway daemon until it receives `SIGINT`/`SIGTERM` (Ctrl-C). Each
  watcher still respects its own gate, so a watcher whose gate is unset stays
  idle.
- **Gate ON with `--once`** — runs a single scheduler tick and exits (the
  legacy pre-daemon behavior), printing a one-line summary of fired/skipped
  jobs.

```sh
MAGI_GATEWAY_DAEMON_ENABLED=1 magi gateway start          # supervise until Ctrl-C
MAGI_GATEWAY_DAEMON_ENABLED=1 magi gateway start --once    # single tick, then exit
```

Channel watchers (Telegram, Discord, …) require explicit provider/client wiring
and are **not** constructed by this local CLI.

## `magi gateway install`

Generates and writes an OS service file (`systemd` unit or `launchd` plist) to
`--target-path`. It does **not** run `systemctl`/`launchctl`, does **not** touch
any system directory, and does **not** set the env gate — installing alone keeps
the daemon a no-op until `MAGI_GATEWAY_DAEMON_ENABLED` is set.

```sh
magi gateway install --target-path ./magi-gateway.service
magi gateway install --target-path ./magi-gateway.plist --manager launchd
magi gateway install --target-path ./magi-gateway.service --exec-path /usr/local/bin/magi
```

| Option | Default | Description |
|--------|---------|-------------|
| `--target-path` | (required) | Where to write the generated unit/plist. |
| `--manager` | auto-detected | `systemd` or `launchd`; auto-detected from the platform when unset. |
| `--exec-path` | `magi` | Path to the `magi` executable used in `ExecStart` / `ProgramArguments`. |

## `magi gateway uninstall`

Removes the service file previously written to `--target-path`. Like
`install`, it does not run `systemctl`/`launchctl`.

```sh
magi gateway uninstall --target-path ./magi-gateway.service
```
