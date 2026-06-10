"""Configuration loading for the pipeline.

The config file is YAML (see config.yaml at the repo root). Relative paths in
the config are resolved against the directory containing the config file, so
the tool behaves the same no matter where it is invoked from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG_NAME = "config.yaml"


#: Named model presets selectable via config (`llm.model`) or CLI (`--model`).
#: "12b" is the spec default; "e4b" is the lighter fallback for weak hardware.
MODEL_PRESETS: dict[str, dict[str, str]] = {
    "12b": {"repo_id": "unsloth/gemma-4-12B-it-qat-GGUF", "quant": "UD-Q4_K_XL"},
    "e4b": {"repo_id": "unsloth/gemma-4-E4B-it-GGUF", "quant": "UD-Q4_K_XL"},
}


@dataclass
class LLMConfig:
    model: str = "12b"
    repo_id: str = "unsloth/gemma-4-12B-it-qat-GGUF"
    quant: str = "UD-Q4_K_XL"
    model_file: str | None = None
    cache_dir: Path = Path("models")
    context_length: int = 16384
    thinking_mode: bool = False
    temperature: float = 1.0
    top_p: float = 0.95
    top_k: int = 64
    ram_warn_gb: float = 12.0


@dataclass
class TTSConfig:
    engine: str = "kokoro"
    voices: list[str] = field(default_factory=lambda: ["af_heart", "am_michael"])
    speed: float = 1.0
    sample_rate: int = 24000
    mp3_bitrate: str = "96k"


@dataclass
class PodcastConfig:
    hosts: int = 2
    target_minutes_min: int = 3
    target_minutes_max: int = 6


@dataclass
class ReportConfig:
    brand_color: str = "0F4761"
    font: str = "Aptos"
    font_fallback: str = "Arial"
    logo_path: Path | None = None
    assets_dir: Path = Path("assets")
    gp_title_template: str = "Weekly Summary: Week {n}"
    fpx_title_template: str = "Assessment Summary: Assessment {n}"


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    podcast: PodcastConfig = field(default_factory=PodcastConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    output_dir: Path = Path("output")
    base_dir: Path = Path(".")

    def resolve(self, p: Path | None) -> Path | None:
        if p is None:
            return None
        p = Path(p)
        return p if p.is_absolute() else (self.base_dir / p).resolve()


def load_config(path: Path | str | None = None) -> AppConfig:
    """Load config.yaml; missing file or missing keys fall back to defaults."""
    if path is None:
        path = Path.cwd() / DEFAULT_CONFIG_NAME
    path = Path(path)
    raw: dict = {}
    if path.is_file():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        base_dir = path.parent.resolve()
    else:
        base_dir = Path.cwd()

    llm_raw = raw.get("llm", {}) or {}
    sampling = llm_raw.get("sampling", {}) or {}
    model = str(llm_raw.get("model", LLMConfig.model)).lower()
    if model not in MODEL_PRESETS:
        known = ", ".join(sorted(MODEL_PRESETS))
        raise ValueError(f"Unknown llm.model {model!r} in {path}; expected one of: {known}")
    preset = MODEL_PRESETS[model]
    llm = LLMConfig(
        model=model,
        # explicit repo_id/quant in the YAML override the preset
        repo_id=llm_raw.get("repo_id", preset["repo_id"]),
        quant=llm_raw.get("quant", preset["quant"]),
        model_file=llm_raw.get("model_file"),
        cache_dir=Path(llm_raw.get("cache_dir", "models")),
        context_length=int(llm_raw.get("context_length", LLMConfig.context_length)),
        thinking_mode=bool(llm_raw.get("thinking_mode", False)),
        temperature=float(sampling.get("temperature", 1.0)),
        top_p=float(sampling.get("top_p", 0.95)),
        top_k=int(sampling.get("top_k", 64)),
        ram_warn_gb=float(llm_raw.get("ram_warn_gb", 12.0)),
    )

    tts_raw = raw.get("tts", {}) or {}
    tts = TTSConfig(
        engine=tts_raw.get("engine", "kokoro"),
        voices=list(tts_raw.get("voices", ["af_heart", "am_michael"])),
        speed=float(tts_raw.get("speed", 1.0)),
        sample_rate=int(tts_raw.get("sample_rate", 24000)),
        mp3_bitrate=str(tts_raw.get("mp3_bitrate", "96k")),
    )

    pod_raw = raw.get("podcast", {}) or {}
    podcast = PodcastConfig(
        hosts=int(pod_raw.get("hosts", 2)),
        target_minutes_min=int(pod_raw.get("target_minutes_min", 3)),
        target_minutes_max=int(pod_raw.get("target_minutes_max", 6)),
    )

    rep_raw = raw.get("report", {}) or {}
    report = ReportConfig(
        brand_color=str(rep_raw.get("brand_color", "0F4761")).lstrip("#"),
        font=rep_raw.get("font", "Aptos"),
        font_fallback=rep_raw.get("font_fallback", "Arial"),
        logo_path=Path(rep_raw["logo_path"]) if rep_raw.get("logo_path") else None,
        assets_dir=Path(rep_raw.get("assets_dir", "assets")),
        gp_title_template=rep_raw.get("gp_title_template", ReportConfig.gp_title_template),
        fpx_title_template=rep_raw.get("fpx_title_template", ReportConfig.fpx_title_template),
    )

    out_raw = raw.get("output", {}) or {}
    cfg = AppConfig(
        llm=llm,
        tts=tts,
        podcast=podcast,
        report=report,
        output_dir=Path(out_raw.get("dir", "output")),
        base_dir=base_dir,
    )
    return cfg


def select_model(cfg: AppConfig, model: str) -> None:
    """Apply a named model preset (CLI --model override)."""
    model = model.lower()
    if model not in MODEL_PRESETS:
        known = ", ".join(sorted(MODEL_PRESETS))
        raise ValueError(f"Unknown model {model!r}; expected one of: {known}")
    preset = MODEL_PRESETS[model]
    cfg.llm.model = model
    cfg.llm.repo_id = preset["repo_id"]
    cfg.llm.quant = preset["quant"]
    cfg.llm.model_file = None
