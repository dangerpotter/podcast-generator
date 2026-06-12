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
from .artifacts import artifact_filename
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
                source=str(mod_dir / artifact_filename(
                    structure["course"]["number"], "summary", m["number"]
                )), module=m["number"],
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
                source=str(out.parent / artifact_filename(
                    structure["course"]["number"], "script", m["number"]
                )), module=m["number"],
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


def cmd_doctor(cfg, args):
    """Check all dependencies and the model cache, then print a status report."""
    import importlib

    checks_ok = True
    any_warnings = False

    def _ok(msg):
        print(f"  OK   {msg}")

    def _warn(msg):
        nonlocal any_warnings
        any_warnings = True
        print(f"  WARN {msg}")

    def _fail(msg):
        nonlocal checks_ok
        checks_ok = False
        print(f"  FAIL {msg}")

    def _info(msg):
        print(f"  .... {msg}")

    # Python version
    pv = sys.version_info
    if pv >= (3, 10):
        _ok(f"Python {pv.major}.{pv.minor}.{pv.micro}")
    else:
        _fail(f"Python {pv.major}.{pv.minor}.{pv.micro} — Python 3.10+ required")

    # llama-cpp-python
    try:
        lc = importlib.import_module("llama_cpp")
        ver = getattr(lc, "__version__", "installed")
        _ok(f"llama-cpp-python {ver}")
    except ImportError:
        _fail(
            "llama-cpp-python not installed or failed to load its native extension.\n"
            "       Install with:  pip install llama-cpp-python\n"
            "       Prebuilt CPU wheel (no compiler needed):\n"
            "         pip install llama-cpp-python "
            "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu\n"
            "       CUDA wheel: https://github.com/abetlen/llama-cpp-python/releases"
        )

    # kokoro
    try:
        kok = importlib.import_module("kokoro")
        ver = getattr(kok, "__version__", "installed")
        _ok(f"kokoro {ver}")
    except ImportError:
        _fail("kokoro not installed.  Install with:  pip install 'kokoro>=0.9.2'")

    # espeak-ng (warn, not fail — summaries/scripts work without it)
    try:
        from .tts import TTSDependencyError, ensure_espeak
        ensure_espeak()
        _ok("espeak-ng found (podcast generation ready)")
    except TTSDependencyError as e:
        _warn(f"espeak-ng not found — podcast (MP3) generation will fail.\n       {e}")

    # RAM
    try:
        import psutil
        mem = psutil.virtual_memory()
        avail_gb = mem.available / 1024 ** 3
        total_gb = mem.total / 1024 ** 3
        req_gb = 12.0 if cfg.llm.model == "12b" else 8.0
        if avail_gb >= req_gb:
            _ok(f"RAM {avail_gb:.1f} GB free / {total_gb:.1f} GB total")
        else:
            _warn(
                f"RAM {avail_gb:.1f} GB free / {total_gb:.1f} GB total — "
                f"the {cfg.llm.model} model wants ~{req_gb:.0f} GB free.\n"
                f"       Switch model:  capella-podcast --model e4b <command>"
            )
    except ImportError:
        _info("psutil unavailable — RAM check skipped")

    # Model cache
    from .llm import _is_main_gguf
    cache_dir = cfg.resolve(cfg.llm.cache_dir)
    if cache_dir and cache_dir.is_dir():
        ggufs = sorted(p for p in cache_dir.rglob("*.gguf") if _is_main_gguf(p.name))
        if ggufs:
            for g in ggufs:
                size_gb = g.stat().st_size / 1024 ** 3
                _ok(f"Cached model: {g.name} ({size_gb:.1f} GB)")
        else:
            _info(f"Model cache at {cache_dir} is empty — will download on first generation run")
    else:
        _info(f"Model cache directory not created yet — will be created on first run")

    print()
    if checks_ok and not any_warnings:
        print("All checks passed — ready to run.")
    elif checks_ok:
        print("Ready, with warning(s) noted above.")
    else:
        print("One or more required checks FAILED — see messages above.")
        sys.exit(1)


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

    sp = sub.add_parser("doctor", help="check all dependencies and model cache (run this first if anything seems broken)")
    sp.set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    if args.model:
        select_model(cfg, args.model)
    args.func(cfg, args)


if __name__ == "__main__":
    main()
