"""End-to-end output tree validation for a course.

Usage: python tests/validate_all.py output/SWK5017 [output/HMSV-FPX8220 ...]

Checks, per module: summary.docx (layout + section set), script.docx
(parseable two-host turns), podcast.mp3 (decodes, sane duration), and that
the manifest records every artifact.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import soundfile as sf

from capella_podcast.docx_reader import read_script_turns
from capella_podcast.docx_validate import validate_docx
from capella_podcast.summary import FPX_HEADINGS, GP_HEADINGS


def check(cond: bool, msg: str, failures: list[str]) -> None:
    print(f"  [{'ok' if cond else 'FAIL'}] {msg}")
    if not cond:
        failures.append(msg)


def validate_course(course_dir: Path, failures: list[str]) -> None:
    print(f"\n== {course_dir}")
    structure = json.loads((course_dir / "course-structure.json").read_text(encoding="utf-8"))
    manifest = json.loads((course_dir / "manifest.json").read_text(encoding="utf-8"))
    course = structure["course"]
    is_gp = course["type"] == "GP"
    headings = GP_HEADINGS if is_gp else FPX_HEADINGS
    title_word = "Weekly Summary" if is_gp else "Assessment Summary"

    for m in structure["modules"]:
        n = m["number"]
        mod_dir = course_dir / f"{course['module_dir_prefix']}-{n:02d}"
        summary = mod_dir / "summary.docx"
        script = mod_dir / "script.docx"
        mp3 = mod_dir / "podcast.mp3"

        problems = validate_docx(
            summary, expected_headings=headings,
            title=f"{title_word}: {course['module_label']} {n}" if False else None,
            min_hyperlinks=1, expect_logo=True, course_number=course["number"],
        ) if summary.is_file() else ["missing"]
        check(not problems, f"{mod_dir.name}/summary.docx valid ({'; '.join(problems) or 'ok'})", failures)

        if script.is_file():
            turns = read_script_turns(script)
            hosts = {h for h, _ in turns}
            words = sum(len(t.split()) for _, t in turns)
            check(len(turns) >= 6 and hosts == {"HOST A", "HOST B"},
                  f"{mod_dir.name}/script.docx: {len(turns)} turns, hosts {sorted(hosts)}", failures)
            check(words >= 300, f"{mod_dir.name}/script.docx: {words} words", failures)
        else:
            check(False, f"{mod_dir.name}/script.docx exists", failures)

        if mp3.is_file():
            try:
                info = sf.info(str(mp3))
                dur = info.frames / info.samplerate
                check(60 <= dur <= 900 and info.samplerate >= 24000,
                      f"{mod_dir.name}/podcast.mp3: {dur/60:.1f} min @ {info.samplerate} Hz", failures)
            except Exception as e:
                check(False, f"{mod_dir.name}/podcast.mp3 decodes ({e})", failures)
        else:
            check(False, f"{mod_dir.name}/podcast.mp3 exists", failures)

        for kind in ("summary", "script", "podcast"):
            key = f"{kind}:module-{n:02d}"
            check(key in manifest["artifacts"], f"manifest records {key}", failures)


def main() -> None:
    failures: list[str] = []
    for arg in sys.argv[1:]:
        validate_course(Path(arg), failures)
    print()
    if failures:
        sys.exit(f"{len(failures)} check(s) FAILED")
    print("All end-to-end checks passed.")


if __name__ == "__main__":
    main()
