import json
from hashlib import sha256
from pathlib import Path

from scripts.build_package_index import render_index


def test_simple_index_contains_immutable_hash(tmp_path: Path) -> None:
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    wheel = wheels / "homebrew_mlflow-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"fixture-wheel")
    (wheels / "homebrew_mlflow_contracts-0.1.0-py3-none-any.whl").write_bytes(
        b"contracts-wheel"
    )
    plugin = wheels / "homebrew_mlflow_plugins-0.1.0-py3-none-any.whl"
    plugin.write_bytes(b"plugin-wheel")

    output = tmp_path / "packages"
    render_index(wheels, output)

    page = (output / "simple" / "homebrew-mlflow" / "index.html").read_text()
    assert f"#sha256={sha256(b'fixture-wheel').hexdigest()}" in page
    assert (output / "files" / wheel.name).read_bytes() == b"fixture-wheel"
    manifest = json.loads((output / "release-manifest.json").read_text(encoding="utf-8"))
    assert manifest["sha256"][wheel.name] == sha256(b"fixture-wheel").hexdigest()
    plugin_page = (output / "simple" / "homebrew-mlflow-plugins" / "index.html").read_text()
    assert plugin.name in plugin_page


def test_simple_index_rejects_incomplete_first_party_wheelhouse(tmp_path: Path) -> None:
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    (wheels / "homebrew_mlflow-0.1.0-py3-none-any.whl").write_bytes(b"fixture-wheel")

    try:
        render_index(wheels, tmp_path / "packages")
    except SystemExit as error:
        assert "homebrew-mlflow-contracts" in str(error)
        assert "homebrew-mlflow-plugins" in str(error)
    else:
        raise AssertionError("incomplete wheelhouse was accepted")
