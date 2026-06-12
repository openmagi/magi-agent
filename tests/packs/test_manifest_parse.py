import textwrap

import pytest
from pydantic import ValidationError

from magi_agent.packs.manifest import PackManifest, load_manifest_from_toml


def _write(tmp_path, body: str):
    p = tmp_path / "pack.toml"
    p.write_text(textwrap.dedent(body))
    return p


def test_load_manifest_from_toml_parses_provides(tmp_path):
    path = _write(
        tmp_path,
        """
        packId = "firstparty.tools"
        displayName = "First-party tools"
        description = "bundled tools"

        [[provides]]
        type = "tool"
        ref = "FileWrite"
        impl = "magi_agent.firstparty.packs.tools.impls:file_write"

        [[provides]]
        type = "control_plane"
        ref = "cp.maxsteps@1"
        impl = "magi_agent.firstparty.packs.controls.impls:MaxStepsBrake"
        priority = 5
        """,
    )
    manifest = load_manifest_from_toml(path)
    assert isinstance(manifest, PackManifest)
    assert manifest.pack_id == "firstparty.tools"
    assert manifest.provides[0].ref == "FileWrite"
    # gate_position default applied
    assert manifest.provides[1].gate_position == "after"


def test_load_manifest_does_not_import_impls(tmp_path, monkeypatch):
    # An impl pointing at a non-importable module must STILL parse: parsing is static.
    path = _write(
        tmp_path,
        """
        packId = "p"
        displayName = "p"

        [[provides]]
        type = "tool"
        ref = "X"
        impl = "this.module.does.not.exist:Symbol"
        """,
    )
    import builtins

    real_import = builtins.__import__

    def _boom(name, *a, **k):  # pragma: no cover - asserts it's never hit for pack impls
        if name.startswith("this.module"):
            raise AssertionError("loader imported impl during static parse")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _boom)
    manifest = load_manifest_from_toml(path)
    assert manifest.provides[0].impl == "this.module.does.not.exist:Symbol"


def test_load_manifest_malformed_toml_raises(tmp_path):
    path = tmp_path / "pack.toml"
    path.write_text("this is = = not toml")
    with pytest.raises(ValueError):
        load_manifest_from_toml(path)


def test_load_manifest_schema_violation_raises(tmp_path):
    path = _write(
        tmp_path,
        """
        packId = "p"
        displayName = "p"

        [[provides]]
        type = "tool"
        ref = "X"
        """,  # missing impl
    )
    with pytest.raises(ValidationError):
        load_manifest_from_toml(path)
