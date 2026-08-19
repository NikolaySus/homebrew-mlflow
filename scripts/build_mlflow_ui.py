from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

MLFLOW_REPOSITORY = "https://github.com/mlflow/mlflow.git"
MLFLOW_COMMIT = "9a1c0d9a9827acd23c7a215f0999e4b0f97e9870"


def run(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)  # noqa: S603


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the pinned read-only Homebrew MLflow browser assets."
    )
    parser.add_argument("--source", type=Path, default=Path("build/mlflow-source"))
    parser.add_argument("--output", type=Path, default=Path("build/mlflow-ui"))
    arguments = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    source = (repository / arguments.source).resolve()
    output = (repository / arguments.output).resolve()
    build_root = (repository / "build").resolve()
    if build_root not in output.parents:
        raise RuntimeError("MLflow UI output must remain under the repository build directory")
    if not source.exists():
        run(
            [
                "git",
                "-c",
                "core.autocrlf=false",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                MLFLOW_REPOSITORY,
                str(source),
            ],
            cwd=repository,
        )
        run(["git", "checkout", "--detach", MLFLOW_COMMIT], cwd=source)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if revision != MLFLOW_COMMIT:
        raise RuntimeError(f"MLflow source is {revision}; expected {MLFLOW_COMMIT}")
    run(["git", "sparse-checkout", "set", "mlflow/server/js"], cwd=source)
    patch = repository / "deploy/mlflow/mlflow-3.15.1-homebrew.patch"
    check = subprocess.run(
        ["git", "apply", "--check", str(patch)], cwd=source, check=False
    )
    if check.returncode == 0:
        run(["git", "apply", str(patch)], cwd=source)
    else:
        reverse = subprocess.run(
            ["git", "apply", "--check", "--reverse", str(patch)],
            cwd=source,
            check=False,
        )
        if reverse.returncode != 0:
            raise RuntimeError("MLflow UI patch does not apply cleanly to the pinned source")
    javascript = source / "mlflow/server/js"
    corepack = "corepack.cmd" if os.name == "nt" else "corepack"
    yarn_release = javascript / "yarn/releases/yarn-4.12.0.cjs"
    if not yarn_release.is_file():
        environment = dict(os.environ)
        environment["YARN_IGNORE_PATH"] = "1"
        subprocess.run(
            [corepack, "yarn", "set", "version", "4.12.0"],
            cwd=javascript,
            check=True,
            env=environment,
        )
    install = [corepack, "yarn", "install"]
    if os.name != "nt":
        install.append("--immutable")
    run(install, cwd=javascript)
    run([corepack, "yarn", "type-check"], cwd=javascript)
    run([corepack, "yarn", "build"], cwd=javascript)
    output.mkdir(parents=True, exist_ok=True)
    for child in output.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    shutil.copytree(javascript / "build", output, dirs_exist_ok=True)
    print(f"Built MLflow {MLFLOW_COMMIT} UI at {output}")


if __name__ == "__main__":
    main()
