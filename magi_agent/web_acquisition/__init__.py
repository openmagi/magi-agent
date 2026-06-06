"""Default-off web acquisition provider boundaries for the ADK migration."""

from magi_agent.web_acquisition.provider_boundary import (
    LocalWebAcquisitionRuntime,
    WebAcquisitionConfig,
    WebAcquisitionRequest,
    WebAcquisitionResult,
    WebAcquisitionSourceRecord,
)
from magi_agent.web_acquisition.provider_router import (
    ProviderRouterConfig,
    WebAcquisitionProviderRouter,
    build_provider_router,
)

__all__ = [
    "LocalWebAcquisitionRuntime",
    "ProviderRouterConfig",
    "WebAcquisitionConfig",
    "WebAcquisitionProviderRouter",
    "WebAcquisitionRequest",
    "WebAcquisitionResult",
    "WebAcquisitionSourceRecord",
    "build_provider_router",
]
