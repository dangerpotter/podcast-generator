"""Validate course-structure.json intermediates against the two known samples.

Run after `capella-podcast ingest` on both sample JSONs:
    python tests/validate_ingest.py output/SWK5017 output/HMSV-FPX8220
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def check(cond: bool, msg: str, failures: list[str]) -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {msg}")
    if not cond:
        failures.append(msg)


def validate(course_dir: Path, failures: list[str]) -> None:
    path = course_dir / "course-structure.json"
    print(f"\n== {path}")
    s = json.loads(path.read_text(encoding="utf-8"))
    course, modules = s["course"], s["modules"]

    if course["number"] == "SWK5017":
        check(course["type"] == "GP", "GP detected", failures)
        check(course["module_dir_prefix"] == "week", "module dirs are week-NN", failures)
        check(len(modules) == 10, f"10 modules (got {len(modules)})", failures)
        weights = {
            a["code"]: a["grade_weight"]
            for m in modules for a in m["activities"] if a["grade_weight"]
        }
        check(weights == {"u04a1": 25.0, "u07a1": 25.0, "u09a1": 30.0},
              f"graded weights match source (got {weights})", failures)
        n_part = sum(1 for m in modules for a in m["activities"]
                     if a["grade_type"] == "PARTICIPATION")
        check(n_part == 10, f"10 participation discussions (got {n_part})", failures)
    elif course["number"] == "HMSV-FPX8220":
        check(course["type"] == "FPX", "FPX detected", failures)
        check(course["module_dir_prefix"] == "assessment", "module dirs are assessment-NN", failures)
        check(len(modules) == 4, f"4 modules (got {len(modules)})", failures)
        graded = [a["code"] for m in modules for a in m["activities"]
                  if a["grade_type"] == "GRADED"]
        check(len(graded) == 4, f"4 graded assessments (got {graded})", failures)
        check(all(a["grade_weight"] is None
                  for m in modules for a in m["activities"]),
              "no fabricated grade weights (FPX export has none)", failures)
    else:
        check(False, f"unexpected course number {course['number']}", failures)

    check(all(m["introduction"]["text"] for m in modules),
          "every module has introduction text", failures)
    check(all(m["activities"] for m in modules),
          "every module has activities", failures)
    check(all(m["number"] == i + 1 for i, m in enumerate(modules)),
          "module numbers are 1-based and ordered", failures)
    n_links = sum(len(a["links"]) for m in modules for a in m["activities"])
    check(n_links > 0, f"activity hyperlinks preserved ({n_links} links)", failures)
    n_res = sum(len(a["resources"]) for m in modules for a in m["activities"]) + \
        sum(len(m["resources"]) for m in modules)
    check(n_res > 0, f"resource references resolved ({n_res} entries)", failures)
    with_url = [r for m in modules
                for r in (m["resources"] + [r for a in m["activities"] for r in a["resources"]])
                if r["url"]]
    check(len(with_url) > 0, f"resolved resources carry URLs ({len(with_url)})", failures)
    html_leak = [t for m in modules for a in m["activities"]
                 if (t := a["text"]) and ("<p" in t or "</" in t or "&nbsp;" in t)]
    check(not html_leak, "no HTML leaked into plain text", failures)
    all_urls = [r["url"] for r in with_url] + [
        l["url"] for m in modules
        for l in m["introduction"]["links"] + [l for a in m["activities"] for l in a["links"]]
    ]
    bad_urls = [u for u in all_urls if "&amp;" in u]
    check(not bad_urls, f"no HTML-escaped URLs ({len(bad_urls)} bad)", failures)


def main() -> None:
    failures: list[str] = []
    for arg in sys.argv[1:]:
        validate(Path(arg), failures)
    print()
    if failures:
        sys.exit(f"{len(failures)} check(s) FAILED")
    print("All ingest checks passed.")


if __name__ == "__main__":
    main()
