"""Track F — OS service install (systemd unit / launchd plist) tests.

Verifies generated unit/plist CONTENT as strings and install/uninstall against
an injected target path (tmp).  NEVER writes to system dirs or runs
``systemctl`` / ``launchctl``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.gateway.service_install import (
    ServiceManager,
    detect_service_manager,
    install_service,
    render_launchd_plist,
    render_systemd_unit,
    uninstall_service,
)


# ---------------------------------------------------------------------------
# detect_service_manager
# ---------------------------------------------------------------------------

def test_detect_linux_systemd() -> None:
    assert detect_service_manager(platform="linux") == ServiceManager.SYSTEMD


def test_detect_macos_launchd() -> None:
    assert detect_service_manager(platform="darwin") == ServiceManager.LAUNCHD


def test_detect_unknown_returns_none() -> None:
    assert detect_service_manager(platform="win32") == ServiceManager.UNSUPPORTED


# ---------------------------------------------------------------------------
# systemd unit content
# ---------------------------------------------------------------------------

def test_systemd_unit_content() -> None:
    unit = render_systemd_unit(exec_start="/usr/bin/magi gateway start")
    assert "[Unit]" in unit
    assert "[Service]" in unit
    assert "[Install]" in unit
    assert "ExecStart=/usr/bin/magi gateway start" in unit
    assert "Restart=on-failure" in unit
    assert "WantedBy=multi-user.target" in unit
    # default-off discipline: the env gate is documented, not forced ON
    assert "MAGI_GATEWAY_DAEMON_ENABLED" in unit


def test_systemd_unit_does_not_force_gate_on() -> None:
    unit = render_systemd_unit(exec_start="/usr/bin/magi gateway start")
    # Must NOT contain an *active* (uncommented) Environment= line that sets the
    # gate truthy.  A commented hint (``#   Environment=...``) is allowed.
    active_lines = [
        ln for ln in unit.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
    ]
    for ln in active_lines:
        assert "MAGI_GATEWAY_DAEMON_ENABLED" not in ln


# ---------------------------------------------------------------------------
# launchd plist content
# ---------------------------------------------------------------------------

def test_launchd_plist_content() -> None:
    plist = render_launchd_plist(
        label="ai.openmagi.gateway",
        program_arguments=["/usr/local/bin/magi", "gateway", "start"],
    )
    assert plist.startswith("<?xml")
    assert "<!DOCTYPE plist" in plist
    assert "<key>Label</key>" in plist
    assert "<string>ai.openmagi.gateway</string>" in plist
    assert "<key>ProgramArguments</key>" in plist
    assert "<string>/usr/local/bin/magi</string>" in plist
    assert "<string>gateway</string>" in plist
    assert "<string>start</string>" in plist
    # not RunAtLoad-forced default-on: KeepAlive present for resilience
    assert "<key>KeepAlive</key>" in plist


def test_launchd_plist_does_not_force_gate_on() -> None:
    plist = render_launchd_plist(
        label="ai.openmagi.gateway",
        program_arguments=["/usr/local/bin/magi", "gateway", "start"],
    )
    assert "MAGI_GATEWAY_DAEMON_ENABLED" not in plist


# ---------------------------------------------------------------------------
# install / uninstall against an injected tmp path (NO system writes)
# ---------------------------------------------------------------------------

def test_install_systemd_writes_to_injected_path(tmp_path: Path) -> None:
    target = tmp_path / "magi-gateway.service"
    written = install_service(
        manager=ServiceManager.SYSTEMD,
        target_path=target,
        exec_start="/usr/bin/magi gateway start",
    )
    assert written == target
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert "ExecStart=/usr/bin/magi gateway start" in content


def test_install_launchd_writes_to_injected_path(tmp_path: Path) -> None:
    target = tmp_path / "ai.openmagi.gateway.plist"
    written = install_service(
        manager=ServiceManager.LAUNCHD,
        target_path=target,
        program_arguments=["/usr/local/bin/magi", "gateway", "start"],
        label="ai.openmagi.gateway",
    )
    assert written == target
    assert target.exists()
    assert "<key>Label</key>" in target.read_text(encoding="utf-8")


def test_uninstall_removes_injected_path(tmp_path: Path) -> None:
    target = tmp_path / "magi-gateway.service"
    target.write_text("dummy", encoding="utf-8")
    removed = uninstall_service(target_path=target)
    assert removed is True
    assert not target.exists()


def test_uninstall_missing_path_is_noop(tmp_path: Path) -> None:
    target = tmp_path / "does-not-exist.service"
    removed = uninstall_service(target_path=target)
    assert removed is False


def test_install_unsupported_manager_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        install_service(
            manager=ServiceManager.UNSUPPORTED,
            target_path=tmp_path / "x",
            exec_start="/usr/bin/magi gateway start",
        )


# ---------------------------------------------------------------------------
# XML escape — apostrophe and other special chars in plist label/args
# ---------------------------------------------------------------------------

def test_launchd_plist_xml_escapes_apostrophe_and_special_chars() -> None:
    """A label/arg containing ' & < must produce &apos; &amp; &lt; in the plist."""
    plist = render_launchd_plist(
        label="com.o'malley.magi",
        program_arguments=["/usr/bin/magi", "run & <check>"],
    )
    # apostrophe in label
    assert "&apos;" in plist
    assert "com.o'malley.magi" not in plist  # raw form must NOT appear
    # ampersand and less-than in program argument
    assert "&amp;" in plist
    assert "&lt;" in plist
    assert "run & <check>" not in plist  # raw form must NOT appear
    # double-escaping guard: & must not become &amp;amp;
    assert "&amp;amp;" not in plist
