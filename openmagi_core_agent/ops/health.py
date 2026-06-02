from __future__ import annotations


def default_runtime_ops_health_metadata() -> dict[str, object]:
    return {
        "schemaVersion": "openmagi.ops.health.v1",
        "enabled": False,
        "source": "local_in_memory",
        "publicProjectionAvailable": True,
        "liveToolExecutionAttached": False,
        "productionStorageAttached": False,
        "productionQueueAttached": False,
    }
