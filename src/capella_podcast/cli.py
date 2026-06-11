"""Command-line interface.

Subcommands (per spec):
  ingest <course.json>          Stage 1: build the intermediate structure.
  summaries [--module N]        Stage 2: Summary Report DOCX per module.
  scripts   [--module N]        Stage 3: Podcast Script DOCX per module.
  podcasts  [--module N]        Stage 4: Podcast MP3 per module.
  regen --from-summary|--from-script --module N
  run-all <course.json>         Full pipeline.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, ingest as ingest_mod, manifest
from .config import MODEL_PRESETS, load_config, select_model


def _course_dir(cfg, args) -> Path:
    """Resolve output/{course.number} for commands that run after ingest."""
    output_dir = cfg.resolve(cfg.output_dir)
    if getattr(args, "course", None):
        return output_dir / args.course
    candidates = [d for d in output_dir.iterdir() if (d / ingest_mod.COURSE_STRUCTURE_NAME).is_file()] if output_dir.is_dir() else []
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        sys.exit("No ingested course found in output/. Run `ingest <course.json>` first.")
    names = ", ".join(d.name for d in candidates)
    sys.exit(f"Multiple ingested courses found ({names}); pick one with --course.")


def cmd_ingest(cfg, args) -> Path:
    src = Path(args.course_json)
    if not src.is_file():
        sys.exit(f"Course JSON not found: {src}")
    try:
        ing = ingest_mod.ingest(src)
    except ingest_mod.CourseTypeError as e:
        sys.exit(f"ERROR: {e}")
    out = ingest_mod.write_structure(ing, cfg.resolve(cfg.output_dir))
    course = ing.structure["course"]
    manifest.record(
        out.parent, "ingest", out, source=str(src.resolve()),
        warnings=ing.warnings, course=course,
    )
    n = len(ing.structure["modules"])
    label = course["module_label"].lower()
    print(f"Detected {'Guided Path' if course['type'] == 'GP' else 'FlexPath'} course "
          f"{course['number']} - {course['name']}")
    print(f"Parsed {n} {label}{'s' if n != 1 else ''} -> {out}")
    if ing.warnings:
        print(f"{len(ing.warnings)} warning(s) noted in the manifest:")
        for w in ing.warnings:
            print(f"  - {w}")
    return out.parent


def _select_modules(structure, args) -> list[dict]:
    modules = structure["modules"]
    if getattr(args, "module", None):
        picked = [m for m in modules if m["number"] == args.module]
        if not picked:
            sys.exit(f"Module {args.module} not found (course has {len(modules)}).")
        return picked
    return modules


def cmd_summaries(cfg, args):
    from .llm import LlamaRunner, ModelProvisioningError
    from .summary import generate_module_summary

    course_dir = _course_dir(cfg, args)
    structure = ingest_mod.load_structure(course_dir)
    runner = LlamaRunner(cfg)
    try:
        for m in _select_modules(structure, args):
            label = f"{structure['course']['module_label']} {m['number']}"
            print(f"Generating summary for {label}: {m['title']}")
            out = generate_module_summary(cfg, runner, structure, m, course_dir)
            manifest.record(
                course_dir, "summary", out,
                source=str(course_dir / ingest_mod.COURSE_STRUCTURE_NAME),
                module=m["number"],
            )
    except ModelProvisioningError as e:
        sys.exit(f"ERROR: {e}")


def cmd_scripts(cfg, args):
    from .llm import LlamaRunner, ModelProvisioningError
    from .script import generate_module_script

    course_dir = _course_dir(cfg, args)
    structure = ingest_mod.load_structure(course_dir)
    runner = LlamaRunner(cfg)
    try:
        for m in _select_modules(structure, args):
            label = f"{structure['course']['module_label']} {m['number']}"
            print(f"Generating script for {label}: {m['title']}")
            out = generate_module_script(cfg, runner, structure, m, course_dir)
            mod_dir = out.parent
            manifest.record(
                course_dir, "script", out,
                source=str(mod_dir / "summary.docx"), module=m["number"],
            )
    except (ModelProvisioningError, FileNotFoundError) as e:
        sys.exit(f"ERROR: {e}")


def cmd_podcasts(cfg, args):
    from .podcast import generate_module_podcast
    from .tts import KokoroTTS, TTSDependencyError

    course_dir = _course_dir(cfg, args)
    structure = ingest_mod.load_structure(course_dir)
    tts = KokoroTTS(cfg)
    try:
        for m in _select_modules(structure, args):
            label = f"{structure['course']['module_label']} {m['number']}"
            print(f"Generating podcast for {label}: {m['title']}")
            out = generate_module_podcast(cfg, tts, structure, m, course_dir)
            manifest.record(
                course_dir, "podcast", out,
                source=str(out.parent / "script.docx"), module=m["number"],
            )
    except (TTSDependencyError, FileNotFoundError) as e:
        sys.exit(f"ERROR: {e}")


def cmd_regen(cfg, args):
    """Regenerate downstream artifacts from an edited DOCX (re-read from disk)."""
    if args.from_summary:
        cmd_scripts(cfg, args)
        cmd_podcasts(cfg, args)
    else:  # --from-script
        cmd_podcasts(cfg, args)


def cmd_gui(cfg, args):
    from .gui.server import run

    run(
        config_path=args.config,
        port=args.port,
        open_browser=not args.no_browser,
        model=args.model,
    )


def cmd_run_all(cfg, args):
    course_dir = cmd_ingest(cfg, args)
    args.course = course_dir.name
    args.module = None
    cmd_summaries(cfg, args)
    cmd_scripts(cfg, args)
    cmd_podcasts(cfg, args)
    print("Full pipeline complete.")


def _add_module_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--module", type=int, default=None, help="module number (default: all)")
    p.add_argument("--course", default=None, help="course number under output/ (default: the only one)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="capella-podcast",
        description="Fully local Capella course podcast generator.",
    )
    p.add_argument("--config", default=None, help="path to config.yaml")
    p.add_argument(
        "--model", default=None, choices=sorted(MODEL_PRESETS),
        help="LLM preset: 12b (default, best quality) or e4b (lighter/faster "
             "for weak hardware); overrides config.yaml",
    )
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("ingest", help="Stage 1: parse course JSON into the intermediate")
    sp.add_argument("course_json")
    sp.set_defaults(func=cmd_ingest)

    for name, func, helptext in (
        ("summaries", cmd_summaries, "Stage 2: generate Summary Report DOCX"),
        ("scripts", cmd_scripts, "Stage 3: generate Podcast Script DOCX"),
        ("podcasts", cmd_podcasts, "Stage 4: generate Podcast MP3"),
    ):
        sp = sub.add_parser(name, help=helptext)
        _add_module_args(sp)
        sp.set_defaults(func=func)

    sp = sub.add_parser("regen", help="regenerate downstream artifacts from an edited DOCX")
    group = sp.add_mutually_exclusive_group(required=True)
    group.add_argument("--from-summary", action="store_true")
    group.add_argument("--from-script", action="store_true")
    sp.add_argument("--module", type=int, required=True)
    sp.add_argument("--course", default=None)
    sp.set_defaults(func=cmd_regen)

    sp = sub.add_parser("run-all", help="full pipeline: ingest -> summaries -> scripts -> podcasts")
    sp.add_argument("course_json")
    sp.set_defaults(func=cmd_run_all)

    sp = sub.add_parser("gui", help="launch the local web GUI (binds 127.0.0.1)")
    sp.add_argument("--port", type=int, default=8765, help="port to serve on (default: 8765)")
    sp.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    sp.set_defaults(func=cmd_gui)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    if args.model:
        select_model(cfg, args.model)
    args.func(cfg, args)


if __name__ == "__main__":
    main()
