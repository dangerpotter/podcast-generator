"""Manifest: records what was generated, when, and from which source.

Lives at output/{course.number}/manifest.json. Skipped/missing data noted by
the parser is carried here too, so a human can audit a run.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_NAME = "manifest.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(course_dir: Path) -> dict:
    path = Path(course_dir) / MANIFEST_NAME
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"course": None, "artifacts": {}}


def save(course_dir: Path, manifest: dict) -> Path:
    path = Path(course_dir) / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def record(
    course_dir: Path,
    kind: str,
    artifact_path: Path,
    source: str,
    module: int | None = None,
    warnings: list[str] | None = None,
    course: dict | None = None,
) -> None:
    """Record one generated artifact (ingest, summary, script, podcast)."""
    course_dir = Path(course_dir)
    manifest = load(course_dir)
    if course is not None:
        manifest["course"] = course
    key = kind if module is None else f"{kind}:module-{module:02d}"
    manifest["artifacts"][key] = {
        "kind": kind,
        "module": module,
        "path": str(Path(artifact_path).resolve()),
        "source": source,
        "generated_at": _now(),
        "warnings": warnings or [],
    }
    save(course_dir, manifest)
