from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def installed_packages() -> list[dict[str, str]]:
    packages: list[dict[str, str]] = []
    for dist in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        version = dist.version
        packages.append({"type": "python-package", "name": name, "version": version})
    return packages


def source_files() -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    for base_name in ("app", "scripts", "tests"):
        base = ROOT / base_name
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                files.append(
                    {
                        "type": "source-file",
                        "path": path.relative_to(ROOT).as_posix(),
                        "sha256": sha256_file(path),
                    }
                )
    return files


def build_sbom() -> dict[str, object]:
    return {
        "schema": "id-app-sbom-v1",
        "python": sys.version.split()[0],
        "packages": installed_packages(),
        "files": source_files(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a local JSON SBOM for the release environment.")
    parser.add_argument("--output", default="dist/id_app.sbom.json")
    args = parser.parse_args()

    output = (ROOT / args.output).resolve()
    root = ROOT.resolve()
    if root != output and root not in output.parents:
        print(f"Refusing to write SBOM outside project: {output}", file=sys.stderr)
        return 1

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_sbom(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
