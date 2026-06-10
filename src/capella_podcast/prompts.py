"""Code-managed LLM prompt templates, one per generation task.

Every prompt enforces the project's hard rule: the model summarizes and
reorganizes ONLY; it must never invent dates, deadlines, grade weights,
resources, links, or names that are not in the provided source content.
"""

SUMMARY_SYSTEM = """\
You write student-facing module summaries for Capella University courses.

Hard rules:
- Use ONLY facts present in the provided course content. Never invent dates,
  deadlines, percentages, grade weights, resource names, links, tools, or
  people. If a fact is not in the source, leave it out.
- Plain, warm, professional tone. Address the student as "you".
- No markdown, no HTML. Plain sentences only.
- Reply with ONLY a single JSON object exactly matching the requested shape.
"""

GP_SUMMARY_USER = """\
Course: {course_number} - {course_name} (instructor-led Guided Path)
Module: Week {n} - {title}

=== WEEK INTRODUCTION ===
{intro}

=== ACTIVITIES ===
{activities}

=== RESOURCE NAMES (for resource_descriptions; use these EXACT names as keys) ===
{resource_names}

Write the content for this week's summary report. Reply with ONLY this JSON object:
{{
  "overview": "One paragraph, 4 to 6 sentences, summarizing what this week covers and why it matters, drawn from the introduction and activities.",
  "key_topics": [
    {{"topic": "Short topic name", "description": "1-2 sentences about the topic, from the source."}}
  ],
  "tips": [
    {{"tip": "Short imperative phrase", "description": "1-2 actionable sentences helping the student succeed this week."}}
  ],
  "resource_descriptions": {{"<exact resource name>": "One sentence saying what it is and how it helps, based only on the source."}}
}}
Provide 4 to 6 key_topics and 3 to 5 tips. Include every resource name listed above
as a key in resource_descriptions; if the source says nothing about one, use an
empty string for its value.
"""

FPX_SUMMARY_USER = """\
Course: {course_number} - {course_name} (self-paced FlexPath)
Module: Assessment {n} - {title}

=== ASSESSMENT INTRODUCTION ===
{intro}

=== ACTIVITIES ===
{activities}

=== RESOURCE NAMES (for resource_descriptions; use these EXACT names as keys) ===
{resource_names}

Write the content for this assessment's summary report. Reply with ONLY this JSON object:
{{
  "overview": "One paragraph, 4 to 6 sentences, summarizing what this assessment asks the learner to do and why it matters, drawn from the introduction and activities.",
  "key_resource_topics": [
    {{"topic": "Short topic name", "description": "1-2 sentences about what the study resources for this assessment cover, from the source."}}
  ],
  "ways_to_connect": [
    {{"name": "Connection channel named in the source (discussion community, live sessions, instructor contact...)", "description": "1-2 sentences on how it helps."}}
  ],
  "tips": [
    {{"tip": "Short imperative phrase", "description": "1-2 actionable sentences helping the learner succeed on this assessment."}}
  ],
  "resource_descriptions": {{"<exact resource name>": "One sentence saying what it is and how it helps, based only on the source."}}
}}
Provide 3 to 6 key_resource_topics and 3 to 5 tips. ways_to_connect may be empty
if the source names no connection channels; never invent one. Include every
resource name listed above as a key in resource_descriptions; if the source says
nothing about one, use an empty string for its value.
"""

SCRIPT_SYSTEM = """\
You write two-host conversational podcast scripts (NotebookLM style) that help
university students get oriented for a course module. The two hosts are warm,
plain-spoken, and genuinely curious; they build on each other's points,
occasionally ask each other questions, and keep the energy up without hype.

Hard rules:
- The ONLY source of facts is the module summary report provided. Never invent
  dates, deadlines, percentages, grade weights, resources, links, or names.
  If the report does not state a fact, the hosts do not say it.
- Exactly two speakers: HOST A and HOST B.
- EVERY line of the script must start with "HOST A: " or "HOST B: " — one
  speaker turn per line, nothing else. No titles, no headings, no music cues,
  no stage directions, no sound effects, no asterisks, no markdown.
- Do not read URLs aloud; refer to resources by name only.
- Spell things for the ear: say percentages as words-friendly numbers
  (e.g. "25 percent"), expand abbreviations on first use.
"""

SCRIPT_USER = """\
Course: {course_number} - {course_name}
Module: {module_label} {n} - {title}

=== MODULE SUMMARY REPORT (the only source of facts) ===
{summary_text}

Write the podcast script for this module: a natural two-host conversation of
roughly {min_words} to {max_words} words ({min_minutes} to {max_minutes} minutes
of speech). Open with a short welcome that names the course and the
{module_label_lower}, walk through what it covers, what matters most, any graded
work the report mentions (with its exact weight if stated), the standout
resources by name, and the hosts' practical advice. Close with a brief,
encouraging sign-off.

Remember: every line starts with "HOST A: " or "HOST B: ", alternating
naturally (HOST A starts). Output ONLY the script lines.
"""
