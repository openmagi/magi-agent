from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_context_packet_import_is_pure_local_only() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.runtime.context_packet")
assert hasattr(module, "ConversationContextPacket")

forbidden_prefixes = (
    "fastapi",
    "starlette",
    "requests",
    "httpx",
    "socket",
    "subprocess",
    "google.adk.runners",
    "google.adk.agents",
    "openmagi_core_agent.tools",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.workspace",
    "kubernetes",
    "supabase",
)
loaded = [
    name for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"context_packet import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_context_packet_source_forbids_live_runtime_imports() -> None:
    source = (
        Path(__file__).parents[1]
        / "openmagi_core_agent"
        / "runtime"
        / "context_packet.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "from fastapi",
        "import fastapi",
        "from google.adk.runners",
        "from google.adk.agents",
        "import requests",
        "import httpx",
        "import socket",
        "import subprocess",
        "ToolDispatcher",
        "Runner(",
        "Agent(",
        "kubectl",
        "os.system",
        "exec(",
        "eval(",
    )
    for marker in forbidden:
        assert marker not in source
