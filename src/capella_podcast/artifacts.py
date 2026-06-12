"""Canonical filenames for generated course artifacts."""

from __future__ import annotations


_ARTIFACT_NAMES = {
    "summary": ("assessment_summary", ".docx"),
    "script": ("podcast_script", ".docx"),
    "podcast": ("podcast_overview", ".mp3"),
}


def artifact_filename(course_id: str, kind: str, module_number: int) -> str:
    """Return the canonical filename for one generated module artifact."""
    try:
        label, extension = _ARTIFACT_NAMES[kind]
    except KeyError as exc:
        raise ValueError(f"unknown artifact kind: {kind!r}") from exc
    return f"cc_{course_id.lower()}_{label}-{module_number:02d}{extension}"
