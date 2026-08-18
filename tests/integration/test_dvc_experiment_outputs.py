from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


def _run(root: Path, *command: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DVC_NO_ANALYTICS"] = "1"
    result = subprocess.run(
        list(command),
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result


def test_dvc_experiment_records_all_declared_output_hashes(tmp_path: Path) -> None:
    if shutil.which("git") is None or shutil.which("dvc") is None:
        pytest.skip("Git and DVC executables are required")
    _run(tmp_path, "git", "init")
    _run(tmp_path, "git", "config", "user.email", "boundary@example.invalid")
    _run(tmp_path, "git", "config", "user.name", "Boundary Test")
    _run(tmp_path, "dvc", "init")
    outputs = ["model.bin", "predictions.csv", "features.json", "summary.txt"]
    (tmp_path / "generate.py").write_text(
        "from pathlib import Path\n"
        + "\n".join(
            f"Path({name!r}).write_text({('content-' + name)!r}, encoding='utf-8')"
            for name in outputs
        )
        + "\nPath('metrics.json').write_text('{\"score\": 1.0}', encoding='utf-8')\n",
        encoding="utf-8",
    )
    (tmp_path / "dvc.yaml").write_text(
        "stages:\n  generate:\n    cmd: python generate.py\n    outs:\n"
        + "".join(f"    - {name}\n" for name in outputs)
        + "    metrics:\n    - metrics.json\n",
        encoding="utf-8",
    )
    _run(tmp_path, "git", "add", ".")
    _run(tmp_path, "git", "commit", "-m", "baseline")

    _run(tmp_path, "dvc", "exp", "run", "-n", "boundary")
    refs = _run(
        tmp_path, "git", "for-each-ref", "--format=%(refname) %(objectname)", "refs/exps"
    ).stdout.splitlines()
    revision = next(
        line.rsplit(" ", 1)[1]
        for line in refs
        if line.split(" ", 1)[0].endswith("/boundary")
    )
    assert revision
    lock = yaml.safe_load(_run(tmp_path, "git", "show", f"{revision}:dvc.lock").stdout)
    locked_outputs = {item["path"]: item for item in lock["stages"]["generate"]["outs"]}

    assert set(outputs) <= set(locked_outputs)
    for name in outputs:
        content = (tmp_path / name).read_bytes()
        assert locked_outputs[name]["md5"] == hashlib.md5(
            content, usedforsecurity=False
        ).hexdigest()
    status = _run(tmp_path, "dvc", "status")
    assert "changed" not in status.stdout.casefold()
