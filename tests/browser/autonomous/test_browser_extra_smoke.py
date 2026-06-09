import importlib.util

import pytest

browser_use_installed = importlib.util.find_spec("browser_use") is not None


@pytest.mark.skipif(not browser_use_installed, reason="browser extra not installed")
def test_browser_use_imports():
    from browser_use import Agent  # noqa: F401
    from browser_use.llm import (  # noqa: F401
        ChatAnthropic,
        ChatGoogle,
        ChatOpenAI,
    )
