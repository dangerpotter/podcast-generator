"""Pipeline actions and course state for the GUI.

Mirrors the cli.py command loops but raises instead of sys.exit, reports
progress on the Job, and supports cooperative cancellation between items.
Heavy imports (llama, kokoro) stay inside the functions that need them.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .. import ingest as ingest_mod, manifest
from ..config import AppConfig
from .jobs import Job

#: stage key -> (artifact kind, output filename)
STAGES = {
    "summaries": ("summary", "summary.docx"),
    "scripts": ("script", "script.docx"),
    "podcasts": ("podcast", "podcast.mp3"),
}
STAGE_ORDER = ("summaries", "scripts", "podcasts")


def do_ingest(cfg: AppConfig, course_json: Path) -> dict:
    """Stage 1, run synchronously (fast). Raises CourseTypeError on bad input."""
    ing = ingest_mod.ingest(course_json)
    out = ingest_mod.write_structure(ing, cfg.resolve(cfg.output_dir))
    course = ing.structure["course"]
    manifest.record(
        out.parent, "ingest", out, source=str(course_json.resolve()),
        warnings=ing.warnings, course=course,
    )
    return {
        "course": course,
        "dir": out.parent.name,
        "modules": len(ing.structure["modules"]),
        "warnings": ing.warnings,
    }


def run_stages(
    job: Job,
    cfg: AppConfig,
    course_dir: Path,
    stages: list[str],
    module: int | None = None,
    only_missing: bool = False,
) -> None:
    """Run the given stages over one module or all of them, with progress."""
    structure = ingest_mod.load_structure(course_dir)
    modules = structure["modules"]
    if module is not None:
        modules = [m for m in modules if m["number"] == module]
        if not modules:
            raise ValueError(f"Module {module} not found (course has {len(structure['modules'])}).")

    stages = [s for s in STAGE_ORDER if s in stages]
    work: list[tuple[str, dict]] = []
    for stage in stages:
        _, fname = STAGES[stage]
        for m in modules:
            out = course_dir / ingest_mod.module_dir_name(structure, m["number"]) / fname
            if only_missing and out.is_file():
                continue
            work.append((stage, m))
    if not work:
        print("Nothing to do: all requested artifacts already exist.")
        return

    runner = None
    tts = None
    label_of = lambda m: f"{structure['course']['module_label']} {m['number']}"  # noqa: E731
    total, done = len(work), 0
    for stage, m in work:
        job.check_cancel()
        kind, _ = STAGES[stage]
        job.set_progress(done, total, f"{kind} — {label_of(m)}")
        print(f"Generating {kind} for {label_of(m)}: {m['title']}")
        if stage == "summaries":
            if runner is None:
                from ..llm import LlamaRunner
                runner = LlamaRunner(cfg)
            from ..summary import generate_module_summary
            out = generate_module_summary(cfg, runner, structure, m, course_dir)
            source = str(course_dir / ingest_mod.COURSE_STRUCTURE_NAME)
        elif stage == "scripts":
            if runner is None:
                from ..llm import LlamaRunner
                runner = LlamaRunner(cfg)
            from ..script import generate_module_script
            out = generate_module_script(cfg, runner, structure, m, course_dir)
            source = str(out.parent / "summary.docx")
        else:
            if tts is None:
                from ..tts import KokoroTTS
                tts = KokoroTTS(cfg)
            from ..podcast import generate_module_podcast
            out = generate_module_podcast(cfg, tts, structure, m, course_dir)
            source = str(out.parent / "script.docx")
        manifest.record(course_dir, kind, out, source=source, module=m["number"])
        done += 1
        job.set_progress(done, total, f"{kind} — {label_of(m)}")
    print(f"Done: {done} artifact{'s' if done != 1 else ''} generated.")


# -- course state -----------------------------------------------------------

def _iso_to_ts(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


def _artifact_info(man: dict, kind: str, module_number: int, path: Path) -> dict:
    entry = (man.get("artifacts") or {}).get(f"{kind}:module-{module_number:02d}") or {}
    info: dict = {
        "exists": path.is_file(),
        "generated_at": entry.get("generated_at"),
        "warnings": entry.get("warnings") or [],
        "edited": False,
        "stale": False,
        "mtime": None,
        "size": None,
    }
    if info["exists"]:
        st = path.stat()
        info["mtime"] = st.st_mtime
        info["size"] = st.st_size
        gen = _iso_to_ts(entry.get("generated_at"))
        # The file is "edited" if its mtime is clearly after the recorded
        # generation time (the user touched it in Word).
        if gen is not None and st.st_mtime > gen + 5:
            info["edited"] = True
    return info


def course_state(cfg: AppConfig, course_dir: Path) -> dict:
    """Full per-module artifact status for one ingested course."""
    structure = ingest_mod.load_structure(course_dir)
    man = manifest.load(course_dir)
    ingest_entry = (man.get("artifacts") or {}).get("ingest") or {}

    modules = []
    for m in structure["modules"]:
        mdir = course_dir / ingest_mod.module_dir_name(structure, m["number"])
        arts = {
            kind: _artifact_info(man, kind, m["number"], mdir / fname)
            for kind, fname in STAGES.values()
        }
        # Downstream artifact is stale when its upstream file is newer.
        for up, down in (("summary", "script"), ("script", "podcast")):
            if (
                arts[up]["exists"] and arts[down]["exists"]
                and arts[up]["mtime"] and arts[down]["mtime"]
                and arts[up]["mtime"] > arts[down]["mtime"] + 1
            ):
                arts[down]["stale"] = True
        modules.append({
            "number": m["number"],
            "title": m["title"],
            "dir": ingest_mod.module_dir_name(structure, m["number"]),
            "notes": m.get("notes") or [],
            "activities": len(m.get("activities") or []),
            "resources": len(m.get("resources") or []),
            "artifacts": arts,
        })

    return {
        "course": structure["course"],
        "dir": course_dir.name,
        "source": ingest_entry.get("source"),
        "ingested_at": ingest_entry.get("generated_at"),
        "warnings": structure.get("warnings") or [],
        "modules": modules,
    }


def list_courses(cfg: AppConfig) -> list[dict]:
    """Light listing of every ingested course under the output dir."""
    output_dir = cfg.resolve(cfg.output_dir)
    courses = []
    if output_dir and output_dir.is_dir():
        for d in sorted(output_dir.iterdir()):
            if not (d / ingest_mod.COURSE_STRUCTURE_NAME).is_file():
                continue
            try:
                structure = ingest_mod.load_structure(d)
            except (OSError, ValueError):
                continue
            course = structure["course"]
            n_modules = len(structure["modules"])
            counts = {}
            for kind, fname in STAGES.values():
                counts[kind] = sum(
                    1 for m in structure["modules"]
                    if (d / ingest_mod.module_dir_name(structure, m["number"]) / fname).is_file()
                )
            courses.append({
                "dir": d.name,
                "name": course.get("name"),
                "number": course.get("number"),
                "type": course.get("type"),
                "module_label": course.get("module_label"),
                "modules": n_modules,
                "counts": counts,
            })
    return courses
