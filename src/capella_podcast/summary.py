"""Stage 2: Summary Report DOCX, one per module.

The LLM writes the prose sections (overview, topics, tips, resource blurbs)
from the structured module data. Facts that must never be wrong — grade
weights, graded-activity names, resource names and URLs — are rendered
deterministically from the source data, not by the LLM.
"""

from __future__ import annotations

from pathlib import Path

from .artifacts import artifact_filename
from .config import AppConfig
from .docx_render import Bullet, Section, find_logo, render_summary_docx
from .docx_validate import validate_docx
from .ingest import module_dir_name
from .llm import LlamaRunner
from .prompts import FPX_SUMMARY_USER, GP_SUMMARY_USER, SUMMARY_SYSTEM

MAX_TEXT_CHARS = 4000

GP_HEADINGS = ["Weekly Overview", "Key Topics", "Important Dates & Deadlines",
               "Recommended Resources", "Tips for Success"]
FPX_HEADINGS = ["Overview", "Key Resource Topics", "Recommended Resources",
                "Ways to Connect", "Tips for Success"]


# -- deterministic content ----------------------------------------------


def collect_resources(module: dict) -> list[dict]:
    """Unit + activity resources and activity-text links, deduped by URL/name."""
    out: list[dict] = []
    seen: set[str] = set()

    def add(name: str | None, url: str | None, desc_hint: str | None = None):
        if not name and not url:
            return
        # Dedupe by URL and by display name (the export repeats resources
        # like a webinar deck under several reference ids/links).
        keys = {k for k in ((url or "").rstrip("/").lower(), (name or "").lower()) if k}
        if keys & seen:
            return
        seen.update(keys)
        out.append({"name": name or url, "url": url, "hint": desc_hint})

    for r in module.get("resources", []):
        add(r.get("name"), r.get("url"), r.get("annotation") or r.get("citation"))
    for a in module.get("activities", []):
        for r in a.get("resources", []):
            add(r.get("name"), r.get("url"), r.get("annotation") or r.get("citation"))
    for a in module.get("activities", []):
        for link in a.get("links", []):
            add(link.get("text"), link.get("url"))
    for link in module.get("introduction", {}).get("links", []):
        add(link.get("text"), link.get("url"))
    return out


def important_dates_bullets(module: dict) -> list[Bullet]:
    """Deterministic Important Dates & Deadlines content (GP only).

    Grade weights and activity names come straight from the export; the LLM
    is never allowed to write this section.
    """
    bullets: list[Bullet] = []
    for a in module.get("activities", []):
        gtype = (a.get("grade_type") or "").upper()
        name = a.get("title") or a.get("code") or "Activity"
        code = f" ({a['code']})" if a.get("code") else ""
        if gtype == "GRADED":
            kind = (a.get("activity_type") or "activity").lower()
            w = a.get("grade_weight")
            if w:
                text = (f"This graded {kind} is worth {w:g}% of your final grade "
                        f"and is due this week.")
            else:
                text = f"This graded {kind} is due this week."
            bullets.append(Bullet(lead_in=f"{name}{code}", text=text))
        elif gtype == "PARTICIPATION":
            bullets.append(Bullet(
                lead_in=f"{name}{code}",
                text="This discussion counts toward your participation grade; "
                     "contribute this week.",
            ))
    if not bullets:
        bullets.append(Bullet(
            lead_in="No graded deadlines",
            text="No graded activities are listed for this module in the course export.",
        ))
    return bullets


def resource_bullets(resources: list[dict], descriptions: dict) -> list[Bullet]:
    bullets = []
    for r in resources:
        desc = (descriptions.get(r["name"]) or "").strip() or (r.get("hint") or "")
        bullets.append(Bullet(lead_in=r["name"], text=desc, url=r.get("url")))
    return bullets


# -- LLM content ----------------------------------------------------------


def _digest_activities(module: dict) -> str:
    parts = []
    for a in module.get("activities", []):
        grade = a.get("grade_type") or "?"
        w = a.get("grade_weight")
        grade += f", {w:g}% of final grade" if w else ""
        head = f"[{a.get('code') or '?'}] {a.get('title') or ''} ({a.get('activity_type') or '?'}; {grade})"
        body = (a.get("text") or "")[:MAX_TEXT_CHARS]
        goal = f"Goal: {a['goal']}\n" if a.get("goal") else ""
        parts.append(f"{head}\n{goal}{body}".strip())
    return "\n\n".join(parts) or "(none)"


def _pairs(items, key_a, key_b) -> list[tuple[str, str]]:
    """Defensively coerce LLM list-of-dicts output into (lead_in, text) pairs."""
    out = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            a = str(item.get(key_a) or "").strip()
            b = str(item.get(key_b) or "").strip()
        else:
            a, b = str(item).strip(), ""
        if a:
            out.append((a, b))
    return out


def generate_summary_content(runner: LlamaRunner, structure: dict, module: dict) -> dict:
    course = structure["course"]
    is_gp = course["type"] == "GP"
    resources = collect_resources(module)
    template = GP_SUMMARY_USER if is_gp else FPX_SUMMARY_USER
    user = template.format(
        course_number=course["number"],
        course_name=course["name"],
        n=module["number"],
        title=module["title"],
        intro=(module["introduction"]["text"] or "(none)")[:MAX_TEXT_CHARS],
        activities=_digest_activities(module),
        resource_names="\n".join(f"- {r['name']}" for r in resources) or "(none)",
    )
    return runner.chat_json(SUMMARY_SYSTEM, user, max_tokens=2048)


# -- assembly -------------------------------------------------------------


def build_sections(structure: dict, module: dict, content: dict) -> list[Section]:
    course = structure["course"]
    resources = collect_resources(module)
    descriptions = content.get("resource_descriptions") or {}
    if not isinstance(descriptions, dict):
        descriptions = {}
    overview = str(content.get("overview") or "").strip()
    tips = [Bullet(a, b) for a, b in _pairs(content.get("tips"), "tip", "description")]

    if course["type"] == "GP":
        topics = [Bullet(a, b) for a, b in _pairs(content.get("key_topics"), "topic", "description")]
        return [
            Section("Weekly Overview", paragraphs=[overview] if overview else []),
            Section("Key Topics", bullets=topics),
            Section("Important Dates & Deadlines", bullets=important_dates_bullets(module)),
            Section("Recommended Resources", bullets=resource_bullets(resources, descriptions)),
            Section("Tips for Success", bullets=tips),
        ]
    topics = [Bullet(a, b) for a, b in _pairs(content.get("key_resource_topics"), "topic", "description")]
    connect = [Bullet(a, b) for a, b in _pairs(content.get("ways_to_connect"), "name", "description")]
    sections = [
        Section("Overview", paragraphs=[overview] if overview else []),
        Section(
            "Key Resource Topics",
            paragraphs=[f"Key topics addressed in the resources for Assessment {module['number']} include:"],
            bullets=topics,
        ),
        Section("Recommended Resources", bullets=resource_bullets(resources, descriptions)),
        Section("Ways to Connect", bullets=connect or [Bullet(
            "Your faculty",
            "Reach out through the courseroom whenever you have questions about this assessment.",
        )]),
        Section("Tips for Success", bullets=tips),
    ]
    return sections


def summary_title(cfg: AppConfig, structure: dict, module_number: int) -> str:
    if structure["course"]["type"] == "GP":
        return cfg.report.gp_title_template.format(n=module_number)
    return cfg.report.fpx_title_template.format(n=module_number)


def generate_module_summary(
    cfg: AppConfig,
    runner: LlamaRunner,
    structure: dict,
    module: dict,
    course_dir: Path,
) -> Path:
    course = structure["course"]
    content = generate_summary_content(runner, structure, module)
    sections = build_sections(structure, module, content)
    title = summary_title(cfg, structure, module["number"])
    subtitle = f"{course['number']} - {course['name']}"
    out = course_dir / module_dir_name(structure, module["number"]) / artifact_filename(
        course["number"], "summary", module["number"]
    )
    render_summary_docx(cfg, out, title, subtitle, course["number"], sections)

    headings = GP_HEADINGS if course["type"] == "GP" else FPX_HEADINGS
    has_links = any(r.get("url") for r in collect_resources(module))
    problems = validate_docx(
        out,
        expected_headings=headings,
        title=title,
        min_hyperlinks=1 if has_links else 0,
        expect_logo=find_logo(cfg) is not None,
        course_number=course["number"],
    )
    if problems:
        raise RuntimeError(
            f"{out} failed validation:\n" + "\n".join(f"  - {p}" for p in problems)
        )
    print(f"  validated OK: {out}")
    return out
