"""Stage 1: ingest the Capella course JSON into the intermediate structure.

The intermediate (``course-structure.json``) is the contract between stages:
course meta plus an ordered list of modules, each carrying its title, overview
source text, activities (with grade weights), and resolved resource links.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .html_text import extract_links, html_to_text

SCHEMA_VERSION = 1

COURSE_STRUCTURE_NAME = "course-structure.json"


class CourseTypeError(Exception):
    """Course model type is unrecognized or internally inconsistent."""


@dataclass
class CourseType:
    key: str               # "GP" | "FPX"
    module_label: str      # "Week" | "Assessment"
    dir_prefix: str        # "week" | "assessment"


GP = CourseType("GP", "Week", "week")
FPX = CourseType("FPX", "Assessment", "assessment")


@dataclass
class Ingested:
    structure: dict
    path: Path | None = None
    warnings: list[str] = field(default_factory=list)


def detect_course_type(course: dict) -> CourseType:
    """GUIDED_PATH2/flexPathAny=False -> GP; FLEX_PATH2/flexPathAny=True -> FPX.

    The two fields are cross-checked; on disagreement or an unknown value we
    stop and report rather than guess (per spec).
    """
    design = course.get("courseDesignModelType")
    flex = course.get("flexPathAny")
    if design == "GUIDED_PATH2" and flex is False:
        return GP
    if design == "FLEX_PATH2" and flex is True:
        return FPX
    raise CourseTypeError(
        f"Cannot determine course model: courseDesignModelType={design!r}, "
        f"flexPathAny={flex!r}. Expected GUIDED_PATH2/false (Guided Path) or "
        f"FLEX_PATH2/true (FlexPath). The two fields disagree or the value is "
        f"unrecognized; refusing to guess."
    )


def _index_by(items: list[dict] | None, key: str = "id") -> dict[Any, dict]:
    out: dict[Any, dict] = {}
    for item in items or []:
        if isinstance(item, dict) and item.get(key) is not None:
            out[item[key]] = item
    return out


def _resource_indexes(data: dict) -> tuple[dict, dict, dict]:
    """Index the three resource collections by their wrapped object ids."""
    refs = {}
    for entry in data.get("resourcesReferences") or []:
        ref = (entry or {}).get("courseResourceReference") or {}
        if ref.get("id") is not None:
            refs[ref["id"]] = entry
    resources = {}
    for entry in data.get("resources") or []:
        res = (entry or {}).get("resource") or {}
        if res.get("id") is not None:
            resources[res["id"]] = res
    formats = _index_by(data.get("resourceFormats"))
    return refs, resources, formats


def _resolve_resource_refs(
    ref_ids: list | None,
    refs: dict,
    resources: dict,
    formats: dict,
    warnings: list[str],
    context: str,
) -> list[dict]:
    """courseResourceReferenceIds -> readable {name, url, type, ...} entries."""
    out: list[dict] = []
    for rid in ref_ids or []:
        entry = refs.get(rid)
        if entry is None:
            warnings.append(f"{context}: resource reference {rid} not found; skipped")
            continue
        ref = entry.get("courseResourceReference") or {}
        resolved: dict[str, Any] = {
            "name": None,
            "url": None,
            "type": None,
            "chapter": ref.get("chapterName") or None,
            "annotation": html_to_text(ref.get("annotation")) or None,
            "citation": None,
        }
        for res_id in entry.get("courseResourceIds") or []:
            res = resources.get(res_id)
            if res is None:
                warnings.append(f"{context}: resource {res_id} not found; skipped")
                continue
            resolved["name"] = resolved["name"] or html_to_text(res.get("resourceName")) or None
            # persistentLinks values arrive HTML-escaped (e.g. &amp; in query strings)
            url = res.get("persistentLinks")
            resolved["url"] = resolved["url"] or (html.unescape(url) if url else None)
            resolved["type"] = resolved["type"] or res.get("type")
        for fmt_id in entry.get("courseResourceFormatIds") or []:
            fmt = formats.get(fmt_id)
            if fmt is None:
                warnings.append(f"{context}: resource format {fmt_id} not found; skipped")
                continue
            # Format fields (title/author/APACitation) carry HTML like <em>.
            title = html_to_text(fmt.get("title")) or None
            author = html_to_text(fmt.get("author")) or None
            publisher = html_to_text(fmt.get("publisher")) or None
            apa = html_to_text(fmt.get("APACitation")) or None
            resolved["name"] = resolved["name"] or title
            if not resolved["citation"]:
                parts = [p for p in (author, title, publisher) if p]
                resolved["citation"] = apa or (", ".join(parts) if parts else None)
        if resolved["name"] or resolved["url"]:
            out.append(resolved)
        else:
            warnings.append(f"{context}: resource reference {rid} had no name or URL; skipped")
    return out


def _parse_activity(
    entry: dict,
    activity_text: dict,
    refs: dict,
    resources: dict,
    formats: dict,
    warnings: list[str],
    context: str,
) -> dict:
    act = entry.get("activity") or {}
    code = act.get("code")
    ctx = f"{context} activity {code or act.get('id')}"
    text_html = None
    text_id = entry.get("activityTextId")
    if text_id is not None:
        text_entry = activity_text.get(text_id)
        if text_entry is None:
            warnings.append(f"{ctx}: activityTextId {text_id} not found")
        else:
            text_html = text_entry.get("text")
    else:
        warnings.append(f"{ctx}: no activity text")
    return {
        "code": code,
        "title": (act.get("title") or "").strip() or None,
        "activity_type": act.get("activityType"),
        "type_code": act.get("typeCodeFromCode") or act.get("typeCode"),
        "grade_type": act.get("gradeType"),
        # FPX exports omit gradeWeight/goal entirely; .get() keeps this safe.
        "grade_weight": act.get("gradeWeight"),
        "goal": html_to_text(act.get("goal")) or None,
        "text": html_to_text(text_html) or None,
        "links": extract_links(text_html),
        "resources": _resolve_resource_refs(
            entry.get("courseResourceReferenceIds"), refs, resources, formats, warnings, ctx
        ),
    }


def _detect_format(data: dict) -> str:
    """Return 'compass' for a Course Compass export, 'standard' for the normal export."""
    if "syllabusContent" in data and "unitContent" in data:
        return "compass"
    return "standard"


def _extract_compass_course_meta(data: dict) -> tuple[str | None, str | None]:
    """Return (course_number, course_name) from a Course Compass export."""
    opening = (
        ((data.get("syllabusContent") or {}).get("courseOverview") or {}).get("openingLanguage") or ""
    ).strip()
    m = re.search(
        r'course[,\s]+([A-Z][A-Z0-9-]{3,})\s*[-–]\s*([^,\n]+)',
        opening,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).upper(), m.group(2).strip()
    return None, None


def _detect_course_type_compass(data: dict) -> CourseType:
    """Infer GP/FPX from the course number embedded in the Compass overview."""
    number, _ = _extract_compass_course_meta(data)
    if number and "FPX" in number.upper():
        return FPX
    raise CourseTypeError(
        "Cannot determine course model from Course Compass export. "
        "Select 'Guided Path' or 'FlexPath' from the Course model dropdown."
    )


def _ingest_compass_format(
    data: dict,
    force_course_type: str | None,
    warnings: list[str],
) -> tuple[CourseType, dict, list[dict]]:
    """Parse a Course Compass export into the shared intermediate structure."""
    if force_course_type == "GP":
        ctype = GP
    elif force_course_type == "FPX":
        ctype = FPX
    else:
        ctype = _detect_course_type_compass(data)

    number, name = _extract_compass_course_meta(data)

    syllabus = data.get("syllabusContent") or {}
    unit_content = data.get("unitContent") or {}
    scoring_content = data.get("scoringGuideContent") or {}

    competencies = (syllabus.get("courseCompetencies") or {}).get("competencies") or []

    grading = syllabus.get("courseGrading") or {}
    assess_list = grading.get("assessments") or []
    if not assess_list:
        warnings.append("no assessments found in syllabusContent.courseGrading")

    modules: list[dict] = []
    for assess in assess_list:
        try:
            num = int(assess.get("assessmentNumber") or 0)
        except (ValueError, TypeError):
            warnings.append(f"skipping assessment with invalid number: {assess!r}")
            continue

        title = (assess.get("title") or "").strip() or f"{ctype.module_label} {num}"
        prefix = f"a{num:02d}"
        context = f"{ctype.module_label.lower()} {num}"
        notes: list[str] = []

        # Overview / introduction
        ov_entry = unit_content.get(f"{prefix}Overview") or {}
        ov_html = ov_entry.get("text") or ""
        intro_text = html_to_text(ov_html) or None
        intro_links = extract_links(ov_html)
        if not intro_text:
            notes.append("no overview text")

        # Scoring guide criteria
        sg = scoring_content.get(f"{prefix}ScoringGuide") or {}
        sg_table = sg.get("table") or {}
        criteria = [
            {
                "criterion": c.get("criterion"),
                "weight": c.get("weight"),
                "non_performance": c.get("nonPerformance"),
                "basic": c.get("basic"),
                "proficient": c.get("proficient"),
                "distinguished": c.get("distinguished"),
            }
            for c in (sg_table.get("criteria") or [])
        ]

        # Primary graded activity (assessment instructions)
        acts: list[dict] = []
        instr_entry = unit_content.get(f"{prefix}Instructions") or {}
        instr_html = instr_entry.get("text") or ""
        instr_text = html_to_text(instr_html) or None
        if instr_text or criteria:
            acts.append({
                "code": prefix,
                "title": (instr_entry.get("title") or title).strip(),
                "activity_type": "assignment",
                "type_code": instr_entry.get("type"),
                "grade_type": instr_entry.get("grading"),
                "grade_weight": None,
                "goal": None,
                "text": instr_text,
                "links": extract_links(instr_html),
                "resources": [],
                "scoring_criteria": criteria,
            })
        else:
            notes.append("no instruction text found")

        # Ungraded summary / study entry
        sum_entry = unit_content.get(f"{prefix}Summary") or {}
        sum_html = sum_entry.get("text") or ""
        sum_text = html_to_text(sum_html) or None
        if sum_text:
            acts.append({
                "code": f"{prefix}_summary",
                "title": (sum_entry.get("title") or "Summary").strip(),
                "activity_type": "study",
                "type_code": sum_entry.get("type"),
                "grade_type": sum_entry.get("grading"),
                "grade_weight": None,
                "goal": None,
                "text": sum_text,
                "links": extract_links(sum_html),
                "resources": [],
            })

        # Resource entries: a01resource1, a01resource2, …
        resources: list[dict] = []
        i = 1
        while True:
            res_entry = unit_content.get(f"{prefix}resource{i}")
            if res_entry is None:
                break
            res_html = res_entry.get("text") or ""
            resources.append({
                "name": (res_entry.get("title") or "").strip() or None,
                "url": None,
                "type": "resource",
                "chapter": None,
                "annotation": html_to_text(res_html) or None,
                "citation": None,
                "links": extract_links(res_html),
            })
            i += 1

        modules.append({
            "number": num,
            "title": title,
            "duration": None,
            "introduction": {"text": intro_text, "links": intro_links},
            "activities": acts,
            "resources": resources,
            "notes": notes,
        })
        warnings.extend(f"{context}: {n}" for n in notes)

    course_meta = {
        "name": name,
        "number": number,
        "credits": None,
        "design_model": "FLEX_PATH2" if ctype == FPX else "GUIDED_PATH2",
        "type": ctype.key,
        "module_label": ctype.module_label,
        "module_dir_prefix": ctype.dir_prefix,
        "competencies": competencies,
    }
    return ctype, course_meta, modules


def ingest(
    course_json_path: Path | str,
    force_course_type: str | None = None,
) -> Ingested:
    """Parse the Capella export into the intermediate structure (in memory).

    force_course_type: "GP" or "FPX" to override auto-detection when the
    source file lacks or has inconsistent courseDesignModelType/flexPathAny.
    """
    course_json_path = Path(course_json_path)
    data = json.loads(course_json_path.read_text(encoding="utf-8"))

    # Course Compass export has a completely different schema — dispatch early.
    if _detect_format(data) == "compass":
        warnings: list[str] = []
        _ctype, course_meta, modules = _ingest_compass_format(data, force_course_type, warnings)
        if not modules:
            warnings.append("course has no units")
        structure = {
            "schema_version": SCHEMA_VERSION,
            "source_file": str(course_json_path.resolve()),
            "course": course_meta,
            "modules": modules,
            "warnings": warnings,
        }
        return Ingested(structure=structure, warnings=warnings)

    # Standard Capella export format.
    course = data.get("course") or {}
    if force_course_type == "GP":
        ctype = GP
    elif force_course_type == "FPX":
        ctype = FPX
    else:
        ctype = detect_course_type(course)

    warnings = []
    introductions = _index_by(data.get("introductions"))
    activity_text = _index_by(data.get("activityText"))
    activities_by_id = {}
    for entry in data.get("activities") or []:
        act = (entry or {}).get("activity") or {}
        if act.get("id") is not None:
            activities_by_id[act["id"]] = entry
    refs, resources, formats = _resource_indexes(data)

    modules: list[dict] = []
    for i, unit_entry in enumerate(data.get("units") or [], start=1):
        unit = (unit_entry or {}).get("unit") or {}
        context = f"{ctype.module_label.lower()} {i}"
        notes: list[str] = []

        intro_text, intro_links = None, []
        intro_id = unit_entry.get("introductionId")
        if intro_id is None:
            notes.append("unit has no introduction")
        else:
            intro = introductions.get(intro_id)
            if intro is None:
                notes.append(f"introduction {intro_id} not found")
            else:
                intro_text = html_to_text(intro.get("text")) or None
                intro_links = extract_links(intro.get("text"))
                if not intro_text:
                    notes.append("introduction is empty")

        acts: list[dict] = []
        for aid in unit_entry.get("activityIds") or []:
            entry = activities_by_id.get(aid)
            if entry is None:
                notes.append(f"activity {aid} not found")
                continue
            acts.append(
                _parse_activity(entry, activity_text, refs, resources, formats, warnings, context)
            )
        if not acts:
            notes.append("unit has no activities")

        modules.append(
            {
                "number": i,
                "title": (unit.get("title") or "").strip() or f"{ctype.module_label} {i}",
                "duration": unit.get("duration"),
                "introduction": {"text": intro_text, "links": intro_links},
                "activities": acts,
                "resources": _resolve_resource_refs(
                    unit_entry.get("courseResourceReferenceIds"),
                    refs, resources, formats, warnings, context,
                ),
                "notes": notes,
            }
        )
        warnings.extend(f"{context}: {n}" for n in notes)

    if not modules:
        warnings.append("course has no units")

    structure = {
        "schema_version": SCHEMA_VERSION,
        "source_file": str(course_json_path.resolve()),
        "course": {
            "name": (course.get("name") or "").strip() or None,
            "number": (course.get("number") or "").strip() or None,
            "credits": course.get("credits"),
            "design_model": course.get("courseDesignModelType"),
            "type": ctype.key,
            "module_label": ctype.module_label,
            "module_dir_prefix": ctype.dir_prefix,
        },
        "modules": modules,
        "warnings": warnings,
    }
    return Ingested(structure=structure, warnings=warnings)


def module_dir_name(structure: dict, module_number: int) -> str:
    prefix = structure["course"]["module_dir_prefix"]
    return f"{prefix}-{module_number:02d}"


def write_structure(ing: Ingested, output_dir: Path) -> Path:
    """Write course-structure.json under output/{course.number}/."""
    number = ing.structure["course"]["number"] or "unknown-course"
    course_dir = Path(output_dir) / number
    course_dir.mkdir(parents=True, exist_ok=True)
    path = course_dir / COURSE_STRUCTURE_NAME
    path.write_text(
        json.dumps(ing.structure, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    ing.path = path
    return path


def load_structure(course_dir: Path) -> dict:
    path = Path(course_dir) / COURSE_STRUCTURE_NAME
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found. Run `ingest <course.json>` first."
        )
    return json.loads(path.read_text(encoding="utf-8"))
