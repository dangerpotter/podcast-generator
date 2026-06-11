"""Embedded LLM: provisioning and in-process inference via llama-cpp-python.

No Ollama, no daemon. On first run the configured GGUF is downloaded from
Hugging Face into the local cache (models/ by default); afterwards everything
is offline. Clear, actionable errors for gated downloads and missing wheels.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import psutil

from .config import AppConfig

_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


class ModelProvisioningError(Exception):
    pass


def _quant_matches(filename: str, quant: str) -> bool:
    return quant.lower().replace("-", "_") in filename.lower().replace("-", "_")


_AUX_PATTERNS = ("mmproj", "mtp", "draft")


def _is_main_gguf(path_in_repo: str) -> bool:
    """Filter out auxiliary GGUFs (vision projectors, MTP heads, draft models)."""
    name = Path(path_in_repo).name.lower()
    return not any(p in name for p in _AUX_PATTERNS)


def _repo_file_prefix(repo_id: str) -> str:
    """'unsloth/gemma-4-12B-it-qat-GGUF' -> 'gemma-4-12b-it-qat'.

    Unsloth GGUF repos name their files after the repo basename, so this
    prefix disambiguates cached files when several models share a quant
    label (e.g. both 12B and E4B publish UD-Q4_K_XL).
    """
    base = repo_id.rsplit("/", 1)[-1].lower()
    return base[:-5] if base.endswith("-gguf") else base


def ensure_model(cfg: AppConfig, quiet: bool = False) -> Path:
    """Return the local GGUF path, downloading it on first run."""
    cache_dir = cfg.resolve(cfg.llm.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    if cfg.llm.model_file:
        explicit = cache_dir / cfg.llm.model_file
        if explicit.is_file():
            return explicit

    # Already cached? Match on model-name prefix AND quant so that two models
    # sharing a quant label (12B vs E4B UD-Q4_K_XL) never collide.
    prefix = _repo_file_prefix(cfg.llm.repo_id)
    cached = sorted(
        p for p in cache_dir.rglob("*.gguf")
        if _is_main_gguf(p.name)
        and p.name.lower().startswith(prefix)
        and _quant_matches(p.name, cfg.llm.quant)
    )
    if cached:
        return cached[0]

    from huggingface_hub import hf_hub_download, list_repo_files
    from huggingface_hub.errors import (
        GatedRepoError,
        LocalEntryNotFoundError,
        RepositoryNotFoundError,
    )

    repo = cfg.llm.repo_id
    try:
        files = list_repo_files(repo)
    except GatedRepoError as e:
        raise ModelProvisioningError(_gated_help(repo)) from e
    except RepositoryNotFoundError as e:
        raise ModelProvisioningError(
            f"Hugging Face repo not found: {repo}\n"
            f"Check llm.repo_id in config.yaml, or drop a GGUF into {cache_dir} "
            f"and set llm.model_file."
        ) from e
    except Exception as e:  # offline, DNS, proxy...
        raise ModelProvisioningError(
            f"Could not reach Hugging Face to download {repo} ({e}).\n"
            f"If you are offline, copy the GGUF manually into {cache_dir} and set "
            f"llm.model_file in config.yaml."
        ) from e

    target = cfg.llm.model_file
    if not target:
        ggufs = [f for f in files if f.lower().endswith(".gguf") and _is_main_gguf(f)]
        matches = sorted(f for f in ggufs if _quant_matches(Path(f).name, cfg.llm.quant))
        if not matches and len(ggufs) == 1:
            # The repo carries a single main model file under a different quant
            # label; using it beats failing (the label is informative only).
            print(
                f"NOTE: no GGUF matching quant {cfg.llm.quant!r} in {repo}; "
                f"using the repo's only main model file {ggufs[0]!r}.",
                file=sys.stderr,
            )
            matches = ggufs
        if not matches:
            available = "\n  ".join(sorted(ggufs)) or "(none)"
            raise ModelProvisioningError(
                f"No GGUF matching quant {cfg.llm.quant!r} in {repo}. Available:\n  {available}\n"
                f"Set llm.quant or llm.model_file in config.yaml."
            )
        target = matches[0]

    if not quiet:
        print(f"Downloading {repo}/{target} -> {cache_dir} (first run only)...")
    try:
        path = hf_hub_download(repo_id=repo, filename=target, local_dir=str(cache_dir))
    except GatedRepoError as e:
        raise ModelProvisioningError(_gated_help(repo)) from e
    except LocalEntryNotFoundError as e:
        raise ModelProvisioningError(
            f"Download of {repo}/{target} failed and no local copy exists ({e})."
        ) from e
    return Path(path)


def _gated_help(repo: str) -> str:
    return (
        f"The model repo {repo} is license-gated on Hugging Face.\n"
        f"To fix, either:\n"
        f"  1. Accept the license at https://huggingface.co/{repo} while logged in,\n"
        f"     then run: hf auth login\n"
        f"  2. Or switch llm.repo_id in config.yaml to an ungated mirror\n"
        f"     (the Unsloth mirrors are usually ungated).\n"
        f"  3. Or manually place the GGUF in the models/ cache and set llm.model_file."
    )


def check_ram(cfg: AppConfig) -> None:
    avail_gb = psutil.virtual_memory().available / 1024**3
    if avail_gb < cfg.llm.ram_warn_gb:
        print(
            f"WARNING: only {avail_gb:.1f} GB RAM free; the 12B Q4 model wants "
            f"~{cfg.llm.ram_warn_gb:.0f} GB. Consider the lighter Gemma 4 E4B GGUF "
            f"(set llm.repo_id/llm.quant in config.yaml).",
            file=sys.stderr,
        )


def _register_cuda_dlls() -> None:
    """Make pip-installed NVIDIA runtime DLLs loadable on Windows.

    The CUDA build of llama-cpp-python needs cudart/cublas DLLs; the
    nvidia-*-cu12 wheels ship them under site-packages/nvidia/*/bin but not
    on the DLL search path. No-op on other platforms or CPU-only installs.
    """
    if sys.platform != "win32":
        return
    import os
    import site

    for sp in {*site.getsitepackages(), site.getusersitepackages()}:
        nvidia = Path(sp) / "nvidia"
        if not nvidia.is_dir():
            continue
        for bin_dir in nvidia.glob("*/bin"):
            try:
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = f"{bin_dir};{os.environ['PATH']}"
            except OSError:
                pass


class LlamaRunner:
    """Lazy in-process llama.cpp wrapper (loaded once, reused per stage)."""

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self._llama = None

    def load(self):
        if self._llama is not None:
            return self._llama
        _register_cuda_dlls()
        try:
            from llama_cpp import Llama
        except ImportError as e:
            msg = (
                "llama-cpp-python is not installed or its native extension failed to load.\n"
                "Prebuilt CPU wheel (no compiler needed):\n"
                "  pip install llama-cpp-python "
                "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu\n"
                "CUDA/Metal wheel: https://github.com/abetlen/llama-cpp-python/releases\n"
                "Run `capella-podcast doctor` to check all dependencies at once."
            )
            raise ModelProvisioningError(msg) from e

        check_ram(self.cfg)
        model_path = ensure_model(self.cfg)
        print(f"Loading model in-process: {model_path.name}", flush=True)
        # Auto-detect acceleration: try full GPU offload first, then step
        # down to partial offload, then pure CPU. The CPU wheel ignores
        # n_gpu_layers entirely, so the first attempt succeeds there.
        last_err: Exception | None = None
        for n_gpu_layers in (-1, 32, 20, 0):
            try:
                self._llama = Llama(
                    model_path=str(model_path),
                    n_ctx=self.cfg.llm.context_length,
                    n_gpu_layers=n_gpu_layers,
                    verbose=False,
                )
                if n_gpu_layers != -1:
                    print(f"(partial GPU offload: {n_gpu_layers} layers)", flush=True)
                return self._llama
            except Exception as e:
                last_err = e
                print(f"(load with n_gpu_layers={n_gpu_layers} failed: {e}; retrying)",
                      file=sys.stderr, flush=True)
        raise ModelProvisioningError(f"Could not load {model_path.name}: {last_err}")

    def chat_json(self, system: str, user: str, max_tokens: int = 2048) -> dict:
        """One chat round constrained to a JSON object; parsed and returned."""
        llama = self.load()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_err: Exception | None = None
        for attempt in range(2):
            out = llama.create_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=self.cfg.llm.temperature,
                top_p=self.cfg.llm.top_p,
                top_k=self.cfg.llm.top_k,
                response_format={"type": "json_object"},
            )
            text = out["choices"][0]["message"]["content"] or ""
            text = _THINK_BLOCK.sub("", text).strip()
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                last_err = e
                messages.append({"role": "assistant", "content": text})
                messages.append(
                    {"role": "user", "content": "That was not valid JSON. Reply again with ONLY the JSON object."}
                )
        raise RuntimeError(f"LLM did not return valid JSON after retry: {last_err}")

    def chat_text(self, system: str, user: str, max_tokens: int = 4096) -> str:
        llama = self.load()
        out = llama.create_chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=self.cfg.llm.temperature,
            top_p=self.cfg.llm.top_p,
            top_k=self.cfg.llm.top_k,
        )
        text = out["choices"][0]["message"]["content"] or ""
        return _THINK_BLOCK.sub("", text).strip()


if __name__ == "__main__":
    # `python -m capella_podcast.llm` pre-downloads the model.
    from .config import load_config

    cfg = load_config(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    try:
        p = ensure_model(cfg)
        print(f"Model ready: {p}")
    except ModelProvisioningError as e:
        sys.exit(f"ERROR: {e}")
