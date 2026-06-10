"""Stage 3: Podcast Script DOCX, one per module.

Generated from the Summary Report DOCX read back from disk (NOT from the raw
JSON), so a user's edits to the report flow through. Speaker turns are marked
machine-parseably as ``HOST A:`` / ``HOST B:`` paragraph prefixes for Stage 4.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from .config import AppConfig
from .docx_reader import HOST_PREFIX, read_docx_text, read_script_turns
from .docx_render import SummaryDocBuilder
from .docx_validate import validate_docx
from .ingest import module_dir_name
from .llm import LlamaRunner
from .prompts import SCRIPT_SYSTEM, SCRIPT_USER

WORDS_PER_MINUTE = 155  # conversational TTS pace

_STAGE_DIRECTION = re.compile(r"[\[\(\*][^\]\)\*]{0,80}[\]\)\*]")


def script_title(structure: dict, n: int) -> str:
    return f"Podcast Script: {structure['course']['module_label']} {n}"


def _clean_line(line: str) -> str:
    """Drop stage directions/markdown the model was told not to produce."""
    line = _STAGE_DIRECTION.sub("", line)
    line = line.replace("**", "").replace("##", "")
    return " ".join(line.split())


def parse_script_lines(raw: str) -> list[tuple[str, str]]:
    turns: list[tuple[str, str]] = []
    for line in raw.splitlines():
        line = _clean_line(line.strip())
        if not line:
            continue
        m = HOST_PREFIX.match(line)
        if m:
            turns.append((m.group(1).upper(), m.group(2).strip()))
        elif turns:
            host, spoken = turns[-1]
            turns[-1] = (host, f"{spoken} {line}".strip())
        # leading junk before the first HOST line is dropped
    return [(h, t) for h, t in turns if t]


def generate_script_text(
    cfg: AppConfig,
    runner: LlamaRunner,
    structure: dict,
    module_number: int,
    module_title: str,
    summary_text: str,
) -> list[tuple[str, str]]:
    course = structure["course"]
    user = SCRIPT_USER.format(
        course_number=course["number"],
        course_name=course["name"],
        module_label=course["module_label"],
        module_label_lower=course["module_label"].lower(),
        n=module_number,
        title=module_title,
        summary_text=summary_text,
        min_minutes=cfg.podcast.target_minutes_min,
        max_minutes=cfg.podcast.target_minutes_max,
        min_words=cfg.podcast.target_minutes_min * WORDS_PER_MINUTE,
        max_words=cfg.podcast.target_minutes_max * WORDS_PER_MINUTE,
    )
    raw = runner.chat_text(SCRIPT_SYSTEM, user, max_tokens=4096)
    turns = parse_script_lines(raw)
    if len(turns) < 6 or len({h for h, _ in turns}) < 2:
        # One retry nudging the format; the parser is forgiving but the
        # script must be a real two-host conversation.
        raw = runner.chat_text(
            SCRIPT_SYSTEM,
            user + "\nYour previous attempt was not formatted correctly. "
                   "EVERY line must start with 'HOST A: ' or 'HOST B: '.",
            max_tokens=4096,
        )
        turns = parse_script_lines(raw)
    if len(turns) < 6 or len({h for h, _ in turns}) < 2:
        raise RuntimeError(
            f"LLM script for module {module_number} is not a parseable two-host "
            f"conversation ({len(turns)} turns)."
        )
    return turns


def render_script_docx(
    cfg: AppConfig,
    path: Path,
    structure: dict,
    module_number: int,
    turns: list[tuple[str, str]],
) -> None:
    course = structure["course"]
    b = SummaryDocBuilder(cfg, course["number"])
    b.add_title(script_title(structure, module_number))
    b.add_subtitle(f"{course['number']} - {course['name']}")
    for host, text in turns:
        p = b.doc.add_paragraph()
        b._styled_run(p, f"{host}: ", 12, bold=True)
        b._styled_run(p, text, 12)
    b.save(path)


def generate_module_script(
    cfg: AppConfig,
    runner: LlamaRunner,
    structure: dict,
    module: dict,
    course_dir: Path,
) -> Path:
    n = module["number"]
    mod_dir = course_dir / module_dir_name(structure, n)
    summary_path = mod_dir / "summary.docx"
    if not summary_path.is_file():
        raise FileNotFoundError(
            f"{summary_path} not found. Generate summaries first "
            f"(`capella-podcast summaries --module {n}`)."
        )
    # Read the report back from disk so user edits flow through.
    summary_text = read_docx_text(summary_path)
    turns = generate_script_text(cfg, runner, structure, n, module["title"], summary_text)

    out = mod_dir / "script.docx"
    render_script_docx(cfg, out, structure, n, turns)

    problems = validate_docx(
        out,
        expected_headings=[],
        title=script_title(structure, n),
        course_number=structure["course"]["number"],
    )
    # Script-specific checks: parseable turns, two hosts, sane length.
    parsed = read_script_turns(out)
    if len(parsed) < 6:
        problems.append(f"only {len(parsed)} parseable speaker turns")
    if len({h for h, _ in parsed}) < 2:
        problems.append("script does not alternate between two hosts")
    words = sum(len(t.split()) for _, t in parsed)
    min_words = int(cfg.podcast.target_minutes_min * WORDS_PER_MINUTE * 0.5)
    if words < min_words:
        problems.append(f"script too short: {words} words (< {min_words})")
    # 'no real Word list numbering' is expected for scripts; ignore that one.
    problems = [p for p in problems if p != "no real Word list numbering found"]
    if problems:
        raise RuntimeError(
            f"{out} failed validation:\n" + "\n".join(f"  - {p}" for p in problems)
        )
    minutes = words / WORDS_PER_MINUTE
    print(f"  validated OK: {out} ({len(parsed)} turns, ~{minutes:.1f} min)")
    return out
