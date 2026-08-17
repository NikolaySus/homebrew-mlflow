from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from pathlib import Path


def normalized_project(filename: str) -> str:
    distribution = filename.split("-")[0]
    return re.sub(r"[-_.]+", "-", distribution).lower()


def render_index(wheel_dir: Path, output_dir: Path) -> None:
    wheels = sorted(wheel_dir.glob("*.whl"))
    if not wheels:
        raise SystemExit(f"no wheels found in {wheel_dir}")

    projects: dict[str, list[tuple[Path, str]]] = {}
    manifest: dict[str, str] = {}
    files_dir = output_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    for wheel in wheels:
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        target = files_dir / wheel.name
        target.write_bytes(wheel.read_bytes())
        manifest[wheel.name] = digest
        projects.setdefault(normalized_project(wheel.name), []).append((target, digest))

    simple_dir = output_dir / "simple"
    simple_dir.mkdir(parents=True, exist_ok=True)
    project_links = []
    for project, artifacts in sorted(projects.items()):
        project_dir = simple_dir / project
        project_dir.mkdir(parents=True, exist_ok=True)
        links = [
            f'<a href="../../files/{html.escape(path.name)}#sha256={digest}">'
            f"{html.escape(path.name)}</a>"
            for path, digest in artifacts
        ]
        (project_dir / "index.html").write_text("\n".join(links) + "\n", encoding="utf-8")
        project_links.append(f'<a href="{project}/">{project}</a>')
    (simple_dir / "index.html").write_text("\n".join(project_links) + "\n", encoding="utf-8")
    (output_dir / "release-manifest.json").write_text(
        json.dumps({"sha256": manifest}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    render_index(args.wheel_dir, args.output_dir)


if __name__ == "__main__":
    main()
