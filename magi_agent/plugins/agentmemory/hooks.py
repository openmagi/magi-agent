from __future__ import annotations


def agentmemory_recall(*_args: object, **_kwargs: object) -> dict[str, object]:
    return {"status": "ok", "hook": "agentmemory.recall", "localOnly": True}


def agentmemory_observe(*_args: object, **_kwargs: object) -> dict[str, object]:
    return {"status": "ok", "hook": "agentmemory.observe", "localOnly": True}
