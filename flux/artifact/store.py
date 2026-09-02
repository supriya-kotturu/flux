"""Save/load artifacts as plain versioned JSON files.

No database — artifacts live under `artifacts/`, git-tracked. A human
reviews a new capability the same way they review any other change: `git
diff`. A calling agent loads one by name. Neither needs more than a
filesystem for the scale this project operates at (see docs/ROADMAP.md §1
on not building infrastructure the brief doesn't reward).
"""

from __future__ import annotations

from pathlib import Path

from flux.artifact.schema import Artifact

DEFAULT_ARTIFACTS_DIR = Path("artifacts")


def save(artifact: Artifact, directory: Path = DEFAULT_ARTIFACTS_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{artifact.name}.json"
    path.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def load(name: str, directory: Path = DEFAULT_ARTIFACTS_DIR) -> Artifact:
    path = directory / f"{name}.json"
    return Artifact.model_validate_json(path.read_text(encoding="utf-8"))
