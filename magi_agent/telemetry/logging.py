from __future__ import annotations


def log_record(level: str, message: str, **fields: object) -> dict[str, object]:
    return {"level": level, "message": message, **fields}
