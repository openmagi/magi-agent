from __future__ import annotations

import pytest

from magi_agent.discovery.models import DiscoveryState, DiscoveryTemplate
from magi_agent.discovery.templates import (
    load_template_pack,
    static_template_provider,
)


@pytest.mark.parametrize("name", ["workspace", "repository"])
def test_pack_loads_and_entries_valid(name: str) -> None:
    pack = load_template_pack(name)  # type: ignore[arg-type]
    assert len(pack) >= 5
    for tpl in pack:
        assert isinstance(tpl, DiscoveryTemplate)
        assert tpl.name.strip()
        assert tpl.pattern.strip()
        assert tpl.evidence_flow.strip()
    # names are unique within a pack.
    names = [t.name for t in pack]
    assert len(names) == len(set(names))


def test_workspace_pack_has_expected_classes() -> None:
    names = {t.name for t in load_template_pack("workspace")}
    assert "Missing Deadline" in names
    assert "Version Conflict" in names


def test_unknown_pack_raises() -> None:
    with pytest.raises(ValueError):
        load_template_pack("bogus")  # type: ignore[arg-type]


def test_static_template_provider_returns_full_library_every_round() -> None:
    templates = load_template_pack("repository")
    provider = static_template_provider(templates)
    # Same full library regardless of state.
    assert provider(DiscoveryState.empty()) == templates
    populated = DiscoveryState.empty().extend([])
    assert provider(populated) == templates
