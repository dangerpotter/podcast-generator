"""Targeted config.yaml edits that keep comments and layout intact.

PyYAML round-tripping would drop every comment in config.yaml, so the GUI
only edits the specific lines it owns: known scalar keys (replaced in place,
trailing comments preserved) and the tts voices block (replaced with a flow
list). After editing, the file is re-parsed with load_config as a safety
check; on failure the original text is restored.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..config import MODEL_PRESETS, load_config

#: dotted key -> coercion. Only these keys are editable from the GUI.
EDITABLE_SCALARS: dict[str, type] = {
    "llm.model": str,
    "llm.context_length": int,
    "llm.thinking_mode": bool,
    "llm.sampling.temperature": float,
    "llm.sampling.top_p": float,
    "llm.sampling.top_k": int,
    "tts.speed": float,
    "tts.mp3_bitrate": str,
    "podcast.target_minutes_min": int,
    "podcast.target_minutes_max": int,
}

_VOICE_RE = re.compile(r"^[a-z]{2}_[a-z]+$")


class ConfigEditError(ValueError):
    pass


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_content(line: str) -> bool:
    s = line.strip()
    return bool(s) and not s.startswith("#")


def _block_end(lines: list[str], start: int, parent_indent: int) -> int:
    """First line at or after `start` whose indent returns to <= parent."""
    for i in range(start, len(lines)):
        if _is_content(lines[i]) and _indent(lines[i]) <= parent_indent:
            return i
    return len(lines)


def _find_key(lines: list[str], key: str, start: int, end: int, min_indent: int) -> int | None:
    pat = re.compile(rf"^(\s*){re.escape(key)}\s*:")
    for i in range(start, end):
        m = pat.match(lines[i])
        if m and _is_content(lines[i]) and len(m.group(1)) >= min_indent:
            return i
    return None


def _locate(lines: list[str], dotted: str) -> int:
    """Line index of the final key of a dotted path like llm.sampling.top_p."""
    parts = dotted.split(".")
    start, end, min_indent = 0, len(lines), 0
    for part in parts[:-1]:
        i = _find_key(lines, part, start, end, min_indent)
        if i is None:
            raise ConfigEditError(f"Could not find '{part}' (for {dotted}) in config.yaml")
        parent_indent = _indent(lines[i])
        start = i + 1
        end = _block_end(lines, start, parent_indent)
        min_indent = parent_indent + 1
    i = _find_key(lines, parts[-1], start, end, min_indent)
    if i is None:
        raise ConfigEditError(f"Could not find '{dotted}' in config.yaml")
    return i


def set_scalar(text: str, dotted: str, value) -> str:
    lines = text.splitlines(keepends=True)
    i = _locate(lines, dotted)
    key = dotted.split(".")[-1]
    m = re.match(
        rf"^(?P<head>\s*{re.escape(key)}\s*:\s*)(?P<val>[^#\r\n]*?)(?P<tail>\s*(#.*)?)(?P<eol>\r?\n?)$",
        lines[i],
    )
    if not m:
        raise ConfigEditError(f"Cannot parse line for '{dotted}': {lines[i]!r}")
    lines[i] = f"{m.group('head')}{_fmt(value)}{m.group('tail')}{m.group('eol')}"
    return "".join(lines)


def set_voices(text: str, voices: list[str]) -> str:
    """Replace the tts voices block with a one-line flow list."""
    lines = text.splitlines(keepends=True)
    i = _locate(lines, "tts.voices")
    indent = _indent(lines[i])
    end = _block_end(lines, i + 1, indent)
    eol = "\r\n" if lines[i].endswith("\r\n") else "\n"
    new = (
        f"{' ' * indent}voices: [{', '.join(voices)}]"
        f"   # one distinct voice per host, order = HOST A, HOST B{eol}"
    )
    return "".join(lines[:i] + [new] + lines[end:])


def apply_settings(config_path: Path, updates: dict) -> None:
    """Validate and apply a dict of dotted-key updates to config.yaml."""
    config_path = Path(config_path)
    if not config_path.is_file():
        raise ConfigEditError(f"config file not found: {config_path}")
    original = config_path.read_text(encoding="utf-8")
    text = original

    for key, value in updates.items():
        if key == "tts.voices":
            if (
                not isinstance(value, list) or len(value) < 2
                or not all(isinstance(v, str) and _VOICE_RE.match(v.strip()) for v in value)
            ):
                raise ConfigEditError("voices must be a list of at least two Kokoro voice ids")
            text = set_voices(text, [v.strip() for v in value])
            continue
        if key not in EDITABLE_SCALARS:
            raise ConfigEditError(f"setting not editable from the GUI: {key}")
        typ = EDITABLE_SCALARS[key]
        try:
            if typ is bool:
                value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
            else:
                value = typ(value)
        except (TypeError, ValueError):
            raise ConfigEditError(f"bad value for {key}: {value!r}") from None
        if key == "llm.model" and value not in MODEL_PRESETS:
            raise ConfigEditError(f"unknown model preset {value!r}; expected one of {sorted(MODEL_PRESETS)}")
        if key == "llm.context_length" and not 1024 <= value <= 262144:
            raise ConfigEditError("context_length must be between 1024 and 262144")
        if key == "tts.speed" and not 0.5 <= value <= 2.0:
            raise ConfigEditError("speed must be between 0.5 and 2.0")
        text = set_scalar(text, key, value)

    config_path.write_text(text, encoding="utf-8")
    try:
        load_config(config_path)  # round-trip safety check
    except Exception:
        config_path.write_text(original, encoding="utf-8")
        raise ConfigEditError("edit produced an invalid config; reverted") from None
