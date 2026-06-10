"""OS service install for the ``magi gateway`` daemon (Track F).

Generates a systemd unit (Linux) or a launchd plist (macOS) and installs it to
an INJECTED target path.  ``detect_service_manager()`` chooses the manager for
the current platform.

Default-off discipline
----------------------
Installing the service does NOT enable always-on by itself: the generated unit
documents ``MAGI_GATEWAY_DAEMON_ENABLED`` but never forces it ON.  With the env
gate unset, ``magi gateway start`` is a no-op even from a running service.

Safety
------
The renderers are pure string builders.  ``install_service`` only ever writes to
the caller-supplied ``target_path`` — it NEVER touches ``/etc/systemd`` or
``~/Library/LaunchAgents`` itself and NEVER runs ``systemctl`` / ``launchctl``.
The operator (or the CLI, with an explicit path) decides where to place the
generated file and whether to enable it.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path


def _xml_escape(value: str) -> str:
    """Minimal XML text/attribute escape (avoids importing ``xml.sax``, which
    transitively pulls ``urllib``/``http``/``socket`` and breaks import-cleanliness).
    """
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


class ServiceManager(str, Enum):
    SYSTEMD = "systemd"
    LAUNCHD = "launchd"
    UNSUPPORTED = "unsupported"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_service_manager(*, platform: str | None = None) -> ServiceManager:
    """Return the service manager for ``platform`` (defaults to ``sys.platform``).

    - ``linux*`` → systemd
    - ``darwin`` → launchd
    - anything else → unsupported
    """
    if platform is None:
        import sys  # noqa: PLC0415 — only needed on the auto-detect path

        platform = sys.platform
    plat = platform.lower()
    if plat.startswith("linux"):
        return ServiceManager.SYSTEMD
    if plat == "darwin":
        return ServiceManager.LAUNCHD
    return ServiceManager.UNSUPPORTED


# ---------------------------------------------------------------------------
# Renderers (pure string builders — content tested directly)
# ---------------------------------------------------------------------------

DEFAULT_SYSTEMD_DESCRIPTION = "Magi Agent always-on gateway daemon"
DEFAULT_LAUNCHD_LABEL = "ai.openmagi.gateway"


def render_systemd_unit(
    *,
    exec_start: str,
    description: str = DEFAULT_SYSTEMD_DESCRIPTION,
    working_directory: str | None = None,
) -> str:
    """Render a systemd unit string for the gateway daemon.

    The unit restarts on any exit (``Restart=always`` — the daemon is a
    long-running supervise loop, so every exit short of ``systemctl stop``
    should be restarted) but does NOT set
    ``Environment=MAGI_GATEWAY_DAEMON_ENABLED=1`` — the env gate is left to the
    operator, so installing the unit alone keeps the daemon a no-op.
    """
    lines = [
        "[Unit]",
        f"Description={description}",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"ExecStart={exec_start}",
        # `gateway start` is a long-running supervise loop, so ANY exit should
        # be restarted to honour the always-on intent (`systemctl stop` is not
        # restarted by systemd).
        "Restart=always",
        "RestartSec=5",
    ]
    if working_directory:
        lines.append(f"WorkingDirectory={working_directory}")
    lines += [
        "# Default-OFF: the daemon is a no-op unless MAGI_GATEWAY_DAEMON_ENABLED",
        "# is set to a truthy value.  Uncomment the next line ONLY to enable",
        "# always-on (each channel/cron watcher still respects its own gate):",
        "#   Environment=MAGI_GATEWAY_DAEMON_ENABLED=1",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ]
    return "\n".join(lines)


def render_launchd_plist(
    *,
    label: str = DEFAULT_LAUNCHD_LABEL,
    program_arguments: list[str],
) -> str:
    """Render a launchd plist string for the gateway daemon (macOS).

    ``KeepAlive`` is set so the service is restarted on crash, but no
    ``EnvironmentVariables`` block sets the gate ON — installing alone keeps the
    daemon a no-op until ``MAGI_GATEWAY_DAEMON_ENABLED`` is exported.
    """
    if not program_arguments:
        raise ValueError("program_arguments must be non-empty")
    arg_xml = "\n".join(
        f"        <string>{_xml_escape(arg)}</string>" for arg in program_arguments
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "    <key>Label</key>\n"
        f"    <string>{_xml_escape(label)}</string>\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        f"{arg_xml}\n"
        "    </array>\n"
        "    <key>RunAtLoad</key>\n"
        "    <true/>\n"
        "    <key>KeepAlive</key>\n"
        "    <true/>\n"
        "</dict>\n"
        "</plist>\n"
    )


# ---------------------------------------------------------------------------
# Install / uninstall (injected path only — no system writes)
# ---------------------------------------------------------------------------

def install_service(
    *,
    manager: ServiceManager,
    target_path: Path,
    exec_start: str | None = None,
    program_arguments: list[str] | None = None,
    label: str = DEFAULT_LAUNCHD_LABEL,
    description: str = DEFAULT_SYSTEMD_DESCRIPTION,
) -> Path:
    """Render + write the service file to ``target_path`` and return it.

    NEVER writes to a system directory of its own accord and NEVER runs
    ``systemctl``/``launchctl`` — the caller chooses ``target_path``.

    Raises ``ValueError`` for an unsupported manager or missing required args.
    """
    if manager is ServiceManager.SYSTEMD:
        if not exec_start:
            raise ValueError("exec_start is required for a systemd unit")
        content = render_systemd_unit(exec_start=exec_start, description=description)
    elif manager is ServiceManager.LAUNCHD:
        if not program_arguments:
            raise ValueError("program_arguments is required for a launchd plist")
        content = render_launchd_plist(label=label, program_arguments=program_arguments)
    else:
        raise ValueError(f"unsupported service manager: {manager}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")
    return target_path


def uninstall_service(*, target_path: Path) -> bool:
    """Remove the service file at ``target_path``.  Returns True if removed.

    No-op (returns False) if the file does not exist.  Does not run
    ``systemctl disable`` / ``launchctl unload`` — that is the operator's call.
    """
    if target_path.exists():
        target_path.unlink()
        return True
    return False


__all__ = [
    "DEFAULT_LAUNCHD_LABEL",
    "DEFAULT_SYSTEMD_DESCRIPTION",
    "ServiceManager",
    "detect_service_manager",
    "install_service",
    "render_launchd_plist",
    "render_systemd_unit",
    "uninstall_service",
]
