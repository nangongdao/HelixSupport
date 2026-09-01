"""Generate a CycloneDX SBOM for the project (Phase 28.5).

Writes ``artifacts/sbom.json`` from ``pyproject.toml`` and the pinned runtime
closure in ``requirements.lock``.  The result describes the deployable
application instead of whichever unrelated packages happen to share the build
machine's Python environment.

Usage:
    python scripts/generate_sbom.py [--out artifacts/sbom.json]
"""

from __future__ import annotations

# pyright: reportAttributeAccessIssue=false, reportCallIssue=false, reportOptionalSubscript=false

import argparse
import sys
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent

import sys
sys.path.insert(0, str(ROOT))
from scripts._console import use_utf8_console  # noqa: E402


def _locked_requirements(lock_path: Path) -> list[tuple[str, str]]:
    locked: list[tuple[str, str]] = []
    for raw_line in lock_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        exact_versions = [
            specifier.version
            for specifier in requirement.specifier
            if specifier.operator == "==" and not specifier.version.endswith(".*")
        ]
        if len(exact_versions) != 1:
            raise ValueError(f"runtime lock entry must use one exact version: {line}")
        locked.append((requirement.name, exact_versions[0]))
    return locked


def generate(
    out_path: Path,
    *,
    project_path: Path = ROOT / "pyproject.toml",
    lock_path: Path = ROOT / "requirements.lock",
) -> None:
    try:
        from cyclonedx.output import make_outputter
        from cyclonedx.schema import OutputFormat, SchemaVersion
        from cyclonedx.model.bom import Bom
        from cyclonedx.model.component import Component, ComponentType
    except ImportError as exc:  # pragma: no cover
        print(
            "cyclonedx-python-lib is not installed; run: pip install cyclonedx-python-lib",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
    bom = Bom()
    project = tomllib.loads(project_path.read_text(encoding="utf-8"))["project"]
    components = [
        (project["name"], project["version"], ComponentType.APPLICATION),
        *(
            (name, version, ComponentType.LIBRARY)
            for name, version in _locked_requirements(lock_path)
        ),
    ]
    for name, version, component_type in sorted(components, key=lambda item: item[0].lower()):
        bom.components.add(
            Component(
                name=name,
                version=version,
                type=component_type,
            )
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    outputter = make_outputter(
        bom=bom,
        output_format=OutputFormat.JSON,
        schema_version=SchemaVersion.V1_4,
    )
    try:
        outputter.output_to_file(str(out_path))
    except FileExistsError:
        # The library refuses to overwrite; write the JSON manually.
        out_path.write_text(outputter.output_as_string(), encoding="utf-8")
    print(f"SBOM written: {out_path} ({len(bom.components)} components)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "artifacts" / "sbom.json"))
    args = parser.parse_args()
    generate(Path(args.out))
    return 0


if __name__ == "__main__":
    use_utf8_console()
    sys.exit(main())
