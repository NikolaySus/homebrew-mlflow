#!/usr/bin/env python3
"""Run a local command through the Homebrew MLflow coordinator."""

from __future__ import annotations

import argparse
import subprocess


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    command = arguments.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a command is required after --")
    completed = subprocess.run(  # noqa: S603 - the expert supplied an argv list; no shell is used
        ["homebrew-mlflow", "run", "--experiment", arguments.experiment, "--", *command],
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
