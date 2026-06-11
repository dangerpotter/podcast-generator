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
    if design is None and flex is None:
        raise CourseTypeError(
            "The file's 'course' section has neither courseDesignModelType nor "
            "flexPathAny, so the course model cannot be determined. This is "
            "usually not the full course content export; re-export the course "
            "from the curriculum tool and pick that JSON."
        )
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


def ingest(course_json_path: Path | str) -> Ingested:
    """Parse a Capella export into the intermediate structure (in memory).

    Two export formats are accepted: the curriculum export (top-level
    ``course``/``units``/``activities``) and the flat course content export
    (top-level ``syllabusContent``/``unitContent``/``courseResources``).
    """
    course_json_path = Path(course_json_path)
    data = json.loads(course_json_path.read_text(encoding="utf-8"))

    if not isinstance(data, dict):
        raise CourseTypeError(
            f"{course_json_path.name} is not a course content export: expected "
            f"a JSON object at the top level, got {type(data).__name__}."
        )
    course = data.get("course")
    if isinstance(course, dict) and course:
        return _ingest_curriculum(data, course_json_path)
    if isinstance(data.get("unitContent"), dict) and data["unitContent"]:
        return _ingest_flat(data, course_json_path)
    keys = ", ".join(list(data)[:12]) or "(none)"
    raise CourseTypeError(
        f"{course_json_path.name} does not look like a Capella course export. "
        f"Top-level keys found: {keys}. Expected either the curriculum export "
        f"(top-level 'course', 'units', 'activities') or the course content "
        f"export (top-level 'syllabusContent', 'unitContent', "
        f"'courseResources')."
    )


def _ingest_curriculum(data: dict, course_json_path: Path) -> Ingested:
    """The curriculum export: course/units/activities with id cross-references."""
    course = data["course"]
    ctype = detect_course_type(course)

    warnings: list[str] = []
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


# -- Flat course content export -------------------------------------------
#
# A second real-world export layout (seen 2026-06): top-level
# syllabusContent / scoringGuideContent / unitContent / courseResources.
# unitContent keys are "a<NN><Role>" (a01Overview, a01Instructions,
# a01resource2, a01Vendor1, a01ScoringGuideLink, ...) so modules are
# assessments -> FlexPath. courseResources[*].activity codes reference those
# entries as u<N>r<M> / u<N>v<M> / a<N>.

_FLAT_KEY = re.compile(r"^a(\d+)[A-Za-z]")
_FLAT_RES_CODE = re.compile(r"^(?:u(\d+)r(\d+)|u(\d+)v(\d+)|a(\d+))$")
_COURSE_CODE = re.compile(r"\b([A-Z]{2,5}-FPX\d{3,5}|[A-Z]{2,5}\d{3,5})\b")
_FLAT_GRADE_TYPES = {
    "graded": "GRADED",
    "ungradedRequired": "UNGRADED_REQUIRED",
    "ungradedOptional": "UNGRADED_OPTIONAL",
}
_MOJIBAKE_HINTS = ("â", "Ã", "Â", "ï»")


def _fix_mojibake(s: str | None) -> str | None:
    """Repair UTF-8 text that was decoded as cp1252/latin-1 at export time.

    The flat export arrives double-encoded ("youâ\x80\x99re" for "you're").
    Only applied when telltale lead bytes are present and the full string
    round-trips strictly, so clean text passes through untouched.
    """
    if not s or not any(h in s for h in _MOJIBAKE_HINTS):
        return s
    for _ in range(3):
        repaired = None
        for enc in ("cp1252", "latin-1"):
            try:
                repaired = s.encode(enc).decode("utf-8")
                break
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
        if repaired is None or repaired == s:
            break
        s = repaired
        if not any(h in s for h in _MOJIBAKE_HINTS):
            break
    return s.replace("\ufeff", "")


def _flat_course_meta(data: dict, warnings: list[str]) -> tuple[str | None, str | None]:
    """Pull "BHA-FPX3001 - Essentials of ..." out of the course overview."""
    overview = (data.get("syllabusContent") or {}).get("courseOverview") or {}
    for raw in (overview.get("openingLanguage"), html_to_text(overview.get("text") or "")):
        src = _fix_mojibake(raw)
        if not src:
            continue
        m = _COURSE_CODE.search(src)
        if not m:
            continue
        number = m.group(1)
        name = None
        tail = re.search(re.escape(number) + r"\s*[-–—:]\s*([^\n.]+)", src)
        if tail:
            name = tail.group(1).strip().rstrip(".").strip() or None
        return number, name
    warnings.append(
        "could not find the course number in syllabusContent.courseOverview; "
        "output will land under 'unknown-course'"
    )
    return None, None


def _flat_resource_key(code: str) -> str | None:
    """courseResources activity code -> lowercase unitContent key."""
    m = _FLAT_RES_CODE.match(code or "")
    if not m:
        return None
    ur_n, ur_m, uv_n, uv_m, a_n = m.groups()
    if ur_n is not None:
        return f"a{int(ur_n):02d}resource{int(ur_m)}"
    if uv_n is not None:
        return f"a{int(uv_n):02d}vendor{int(uv_m)}"
    return f"a{int(a_n):02d}instructions"


def _flat_resolve_resource(rid: str, res: dict | None) -> dict | None:
    if not isinstance(res, dict):
        return None
    name = _fix_mojibake(html_to_text(res.get("name") or res.get("title") or "")) or None
    url = res.get("link") or res.get("mediaLink") or res.get("downloadLink")
    # Links arrive HTML-escaped (&amp; in query strings), like persistentLinks.
    url = html.unescape(url) if url else None
    if not name and not url:
        return None
    return {
        "name": name,
        "url": url,
        "type": res.get("type") or res.get("category"),
        "chapter": None,
        "annotation": None,
        "citation": None,
    }


def _ingest_flat(data: dict, course_json_path: Path) -> Ingested:
    """The flat course content export. Assessment-structured, so FlexPath."""
    warnings: list[str] = []
    unit_content = data["unitContent"]

    parsed: dict[str, tuple[int, dict]] = {}
    bad_keys: list[str] = []
    for key, entry in unit_content.items():
        m = _FLAT_KEY.match(key)
        if m and isinstance(entry, dict):
            parsed[key] = (int(m.group(1)), entry)
        else:
            bad_keys.append(key)
    if not parsed:
        keys = ", ".join(list(unit_content)[:12]) or "(none)"
        raise CourseTypeError(
            f"{course_json_path.name}: unitContent has no a<NN>... assessment "
            f"entries (found: {keys}). Only assessment-based (FlexPath) files "
            f"of this layout have been seen; cannot determine the course model."
        )
    if bad_keys:
        warnings.append(
            "unitContent entries not understood and skipped: " + ", ".join(bad_keys)
        )

    ctype = FPX
    number, name = _flat_course_meta(data, warnings)

    # Attach courseResources to unitContent entries via their activity codes.
    course_resources = data.get("courseResources") or {}
    key_by_lc = {k.lower(): k for k in parsed}
    entry_rids = {
        next(iter(d))
        for _, entry in parsed.values()
        for d in entry.get("resources") or []
        if isinstance(d, dict) and d
    }
    rids_by_key: dict[str, list[str]] = {}
    unattached: list[str] = []
    for rid, res in course_resources.items():
        attached = rid in entry_rids
        for code in (res or {}).get("activity") or []:
            target = _flat_resource_key(code)
            actual = key_by_lc.get(target) if target else None
            if actual:
                rids_by_key.setdefault(actual, []).append(rid)
                attached = True
        if not attached and isinstance(res, dict):
            nm = _fix_mojibake(html_to_text(res.get("name") or res.get("title") or ""))
            unattached.append(nm or rid)
    if unattached:
        warnings.append(
            "course-level resources not tied to an assessment were skipped: "
            + ", ".join(unattached)
        )

    # Assessment titles from the syllabus grading block.
    titles: dict[int, str] = {}
    grading = (data.get("syllabusContent") or {}).get("courseGrading") or {}
    for a in grading.get("assessments") or []:
        try:
            n = int((a or {}).get("assessmentNumber"))
        except (TypeError, ValueError):
            continue
        title = _fix_mojibake((a.get("title") or "").strip())
        if title:
            titles.setdefault(n, title)

    modules: list[dict] = []
    for n in sorted({num for num, _ in parsed.values()}):
        context = f"{ctype.module_label.lower()} {n}"
        notes: list[str] = []
        intro_text, intro_links, overview_title = None, [], None
        acts: list[dict] = []

        for key, (num, entry) in parsed.items():
            if num != n:
                continue
            text_html = _fix_mojibake(entry.get("text"))
            title = _fix_mojibake((entry.get("title") or "").strip()) or None
            etype = entry.get("type")
            if etype == "introduction" and intro_text is None:
                overview_title = title
                intro_text = html_to_text(text_html) or None
                intro_links = extract_links(text_html)
                if not intro_text:
                    notes.append("introduction is empty")
                continue
            if etype is None and not (text_html or "").strip():
                continue  # aNNScoringGuideLink stubs carry no content

            res_list: list[dict] = []
            rids = [next(iter(d)) for d in entry.get("resources") or []
                    if isinstance(d, dict) and d]
            rids += rids_by_key.get(key, [])
            for rid in dict.fromkeys(rids):
                resolved = _flat_resolve_resource(rid, course_resources.get(rid))
                if resolved is None:
                    notes.append(f"resource {rid} not found or empty; skipped")
                else:
                    res_list.append(resolved)
            # Relative ./Course_Files/... hrefs are meaningless outside the
            # courseroom; the same files arrive via courseResources with real
            # download URLs.
            links = [l for l in extract_links(text_html)
                     if l["url"].startswith(("http://", "https://"))]
            acts.append({
                "code": key,
                "title": title,
                "activity_type": etype,
                "type_code": None,
                "grade_type": _FLAT_GRADE_TYPES.get(
                    entry.get("grading"), entry.get("grading")
                ),
                "grade_weight": None,
                "goal": None,
                "text": html_to_text(text_html) or None,
                "links": links,
                "resources": res_list,
            })

        if intro_text is None and not intro_links:
            notes.append("unit has no introduction")
        if not acts:
            notes.append("unit has no activities")
        modules.append({
            "number": n,
            "title": titles.get(n) or overview_title or f"{ctype.module_label} {n}",
            "duration": None,
            "introduction": {"text": intro_text, "links": intro_links},
            "activities": acts,
            "resources": [],
            "notes": notes,
        })
        warnings.extend(f"{context}: {x}" for x in notes)

    structure = {
        "schema_version": SCHEMA_VERSION,
        "source_file": str(course_json_path.resolve()),
        "course": {
            "name": name,
            "number": number,
            "credits": None,
            "design_model": None,
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
