"""Local web GUI server.

Stdlib only: ThreadingHTTPServer bound to 127.0.0.1 serves the static
single-page app plus a small JSON API. Generation runs on the JobManager
worker thread; everything else is handled inline on the request thread.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import __version__, ingest as ingest_mod
from ..artifacts import artifact_filename
from ..config import DEFAULT_CONFIG_NAME, MODEL_PRESETS, load_config, select_model
from . import actions
from .config_edit import ConfigEditError, apply_settings
from .jobs import JobManager

STATIC_DIR = Path(__file__).parent / "static"

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

ARTIFACT_MIME = {
    "summary": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "script": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "podcast": "audio/mpeg",
}

#: Kokoro v1.0 English voices offered in the settings UI (free-text also allowed).
VOICE_OPTIONS = [
    {"id": "af_heart", "label": "Heart — US female"},
    {"id": "af_bella", "label": "Bella — US female"},
    {"id": "af_nicole", "label": "Nicole — US female"},
    {"id": "af_sarah", "label": "Sarah — US female"},
    {"id": "af_sky", "label": "Sky — US female"},
    {"id": "af_nova", "label": "Nova — US female"},
    {"id": "am_michael", "label": "Michael — US male"},
    {"id": "am_adam", "label": "Adam — US male"},
    {"id": "am_eric", "label": "Eric — US male"},
    {"id": "am_liam", "label": "Liam — US male"},
    {"id": "am_onyx", "label": "Onyx — US male"},
    {"id": "am_puck", "label": "Puck — US male"},
    {"id": "bf_emma", "label": "Emma — UK female"},
    {"id": "bf_isabella", "label": "Isabella — UK female"},
    {"id": "bm_george", "label": "George — UK male"},
    {"id": "bm_lewis", "label": "Lewis — UK male"},
]

_SAFE_NAME = re.compile(r"^[\w .()&+-]+$")
_MODULE_DIR = re.compile(r"^(week|assessment)-\d{2}$")

STAGE_ACTIONS = {
    "summaries": (["summaries"], "Summaries"),
    "scripts": (["scripts"], "Scripts"),
    "podcasts": (["podcasts"], "Podcasts"),
    "pipeline": (["summaries", "scripts", "podcasts"], "Full pipeline"),
    "regen-summary": (["scripts", "podcasts"], "Regen from edited summary"),
    "regen-script": (["podcasts"], "Regen from edited script"),
}


class AppState:
    def __init__(self, config_path: Path, model_override: str | None = None):
        self.config_path = Path(config_path)
        self.model_override = model_override
        self.jobs = JobManager()
        self.browse_lock = threading.Lock()

    def cfg(self):
        cfg = load_config(self.config_path if self.config_path.is_file() else None)
        if self.model_override:
            select_model(cfg, self.model_override)
        return cfg

    def course_dir(self, cfg, name: str) -> Path:
        if not name or not _SAFE_NAME.match(name) or ".." in name:
            raise ValueError(f"bad course name: {name!r}")
        output_dir = cfg.resolve(cfg.output_dir)
        d = output_dir / name
        if not (d / ingest_mod.COURSE_STRUCTURE_NAME).is_file():
            raise FileNotFoundError(f"no ingested course named {name!r}")
        return d


class Handler(BaseHTTPRequestHandler):
    state: AppState  # set by run()
    protocol_version = "HTTP/1.1"

    # -- plumbing ----------------------------------------------------------
    def log_message(self, fmt, *args):  # silence per-request console noise
        pass

    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"error": message}, status)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _send_file(self, path: Path, mime: str, download_name: str | None = None) -> None:
        data_len = path.stat().st_size
        range_header = self.headers.get("Range")
        start, end = 0, data_len - 1
        status = 200
        if range_header:
            m = re.match(r"bytes=(\d*)-(\d*)$", range_header.strip())
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), data_len - 1)
                else:  # suffix range: last N bytes
                    start = max(0, data_len - int(m.group(2)))
                if start > end or start >= data_len:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{data_len}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{data_len}")
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        with path.open("rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    # -- artifact path resolution -------------------------------------------
    def _artifact_path(self, q: dict) -> tuple[Path, str]:
        course = (q.get("course") or [""])[0]
        mdir = (q.get("dir") or [""])[0]
        kind = (q.get("kind") or [""])[0]
        if kind not in ARTIFACT_MIME:
            raise ValueError(f"bad kind: {kind!r}")
        if not _MODULE_DIR.match(mdir):
            raise ValueError(f"bad module dir: {mdir!r}")
        cfg = self.state.cfg()
        course_dir = self.state.course_dir(cfg, course)
        structure = ingest_mod.load_structure(course_dir)
        module_number = int(mdir.rsplit("-", 1)[1])
        path = course_dir / mdir / artifact_filename(
            structure["course"]["number"], kind, module_number
        )
        if not path.is_file():
            raise FileNotFoundError(f"{path.name} not generated yet for {mdir}")
        return path, kind

    # -- GET -----------------------------------------------------------------
    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            route = parsed.path
            q = urllib.parse.parse_qs(parsed.query)
            if route == "/" or route == "/index.html":
                self._static("index.html")
            elif route.startswith("/static/"):
                self._static(route[len("/static/"):])
            elif route == "/api/state":
                self._get_state()
            elif route == "/api/course":
                self._get_course(q)
            elif route == "/api/job":
                self._get_job(q)
            elif route == "/api/config":
                self._get_config()
            elif route == "/api/file":
                path, kind = self._artifact_path(q)
                name = None
                if (q.get("download") or ["0"])[0] == "1":
                    course = (q.get("course") or [""])[0]
                    mdir = (q.get("dir") or [""])[0]
                    name = path.name
                self._send_file(path, ARTIFACT_MIME[kind], download_name=name)
            elif route == "/api/logo":
                self._get_logo()
            else:
                self._error("not found", 404)
        except (ValueError, FileNotFoundError) as e:
            self._error(str(e), 404 if isinstance(e, FileNotFoundError) else 400)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001 - request boundary
            try:
                self._error(f"internal error: {e}", 500)
            except Exception:
                pass

    def _static(self, rel: str) -> None:
        rel = rel.split("?")[0]
        if ".." in rel or rel.startswith(("/", "\\")):
            return self._error("not found", 404)
        path = STATIC_DIR / rel
        if not path.is_file():
            return self._error("not found", 404)
        self._send_file(path, MIME.get(path.suffix.lower(), "application/octet-stream"))

    def _get_state(self) -> None:
        cfg = self.state.cfg()
        job = self.state.jobs.latest()
        self._json({
            "version": __version__,
            "model": cfg.llm.model,
            "busy": self.state.jobs.busy(),
            "job": job.summary() if job else None,
            "courses": actions.list_courses(cfg),
        })

    def _get_course(self, q: dict) -> None:
        cfg = self.state.cfg()
        course_dir = self.state.course_dir(cfg, (q.get("dir") or [""])[0])
        self._json(actions.course_state(cfg, course_dir))

    def _get_job(self, q: dict) -> None:
        job_id = (q.get("id") or [""])[0]
        job = self.state.jobs.get(int(job_id)) if job_id.isdigit() else self.state.jobs.latest()
        if job is None:
            return self._json({"job": None, "lines": [], "cursor": 0})
        since = (q.get("since") or ["0"])[0]
        lines, cursor = job.log_since(int(since) if since.isdigit() else 0)
        self._json({"job": job.summary(), "lines": lines, "cursor": cursor})

    def _get_config(self) -> None:
        cfg = self.state.cfg()
        self._json({
            "config_path": str(self.state.config_path),
            "model_override": self.state.model_override,
            "presets": sorted(MODEL_PRESETS),
            "voice_options": VOICE_OPTIONS,
            "values": {
                "llm.model": cfg.llm.model,
                "llm.context_length": cfg.llm.context_length,
                "llm.thinking_mode": cfg.llm.thinking_mode,
                "llm.sampling.temperature": cfg.llm.temperature,
                "llm.sampling.top_p": cfg.llm.top_p,
                "llm.sampling.top_k": cfg.llm.top_k,
                "tts.voices": cfg.tts.voices,
                "tts.speed": cfg.tts.speed,
                "tts.mp3_bitrate": cfg.tts.mp3_bitrate,
                "podcast.target_minutes_min": cfg.podcast.target_minutes_min,
                "podcast.target_minutes_max": cfg.podcast.target_minutes_max,
            },
        })

    def _get_logo(self) -> None:
        cfg = self.state.cfg()
        logo = cfg.resolve(cfg.report.logo_path) if cfg.report.logo_path else None
        if logo is None or not Path(logo).is_file():
            assets = cfg.resolve(cfg.report.assets_dir)
            logo = None
            if assets and assets.is_dir():
                for p in sorted(assets.glob("*.png")):
                    n = p.name.lower()
                    if "capella" in n and "logo" in n:
                        logo = p
                        break
        if logo is None:
            return self._error("no logo", 404)
        self._send_file(Path(logo), "image/png")

    # -- POST ----------------------------------------------------------------
    def do_POST(self):
        try:
            route = urllib.parse.urlparse(self.path).path
            body = self._body()
            if route == "/api/browse":
                self._post_browse()
            elif route == "/api/ingest":
                self._post_ingest(body)
            elif route == "/api/run":
                self._post_run(body)
            elif route == "/api/job/cancel":
                self._post_cancel(body)
            elif route == "/api/open":
                self._post_open(body)
            elif route == "/api/config":
                self._post_config(body)
            else:
                self._error("not found", 404)
        except (ValueError, FileNotFoundError) as e:
            self._error(str(e), 404 if isinstance(e, FileNotFoundError) else 400)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001 - request boundary
            try:
                self._error(f"internal error: {e}", 500)
            except Exception:
                pass

    def _post_browse(self) -> None:
        """Open a native file-picker on this machine (the GUI is local-only)."""
        with self.state.browse_lock:
            try:
                import tkinter as tk
                from tkinter import filedialog
            except ImportError:
                return self._json({"path": None, "unsupported": True})
            try:
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                try:
                    path = filedialog.askopenfilename(
                        parent=root,
                        title="Select Capella course export file",
                        filetypes=[
                            ("Course export files", "*.json *.txt"),
                            ("JSON files", "*.json"),
                            ("Text files", "*.txt"),
                            ("All files", "*.*"),
                        ],
                    )
                finally:
                    root.destroy()
            except Exception:
                return self._json({"path": None, "unsupported": True})
        self._json({"path": path or None})

    def _post_ingest(self, body: dict) -> None:
        raw = (body.get("path") or "").strip().strip('"')
        if not raw:
            return self._error("no file path given")
        src = Path(raw)
        if not src.is_file():
            return self._error(f"file not found: {src}")
        course_type = (body.get("course_type") or "").strip() or None
        if course_type not in (None, "GP", "FPX"):
            return self._error(f"invalid course_type: {course_type!r}")
        cfg = self.state.cfg()
        try:
            result = actions.do_ingest(cfg, src, force_course_type=course_type)
        except ingest_mod.CourseTypeError as e:
            return self._error(str(e))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return self._error(f"not a valid course export file: {e}")
        self._json(result)

    def _post_run(self, body: dict) -> None:
        action = body.get("action")
        if action not in STAGE_ACTIONS:
            return self._error(f"unknown action: {action!r}")
        if self.state.jobs.busy():
            return self._error("a job is already running", 409)
        stages, action_label = STAGE_ACTIONS[action]
        cfg = self.state.cfg()
        course_dir = self.state.course_dir(cfg, body.get("course") or "")
        module = body.get("module")
        if module is not None:
            module = int(module)
        only_missing = bool(body.get("only_missing"))

        structure = ingest_mod.load_structure(course_dir)
        scope = f"{structure['course']['module_label']} {module}" if module else "all modules"
        title = f"{action_label} — {scope} — {course_dir.name}"

        def fn(job, cfg=cfg, course_dir=course_dir, stages=stages, module=module, only_missing=only_missing):
            actions.run_stages(job, cfg, course_dir, stages, module=module, only_missing=only_missing)

        job = self.state.jobs.submit(title, fn)
        self._json({"job": job.summary()})

    def _post_cancel(self, body: dict) -> None:
        job_id = body.get("id")
        job = self.state.jobs.get(int(job_id)) if job_id else self.state.jobs.latest()
        if job is None:
            return self._error("no job to cancel", 404)
        job.request_cancel()
        self._json({"job": job.summary()})

    def _post_open(self, body: dict) -> None:
        """Open a generated file or folder with the OS default app (local-only)."""
        target = body.get("target")
        cfg = self.state.cfg()
        if target == "config":
            path = self.state.config_path
        elif target == "output":
            path = cfg.resolve(cfg.output_dir)
        elif target == "course":
            path = self.state.course_dir(cfg, body.get("course") or "")
        elif target == "module":
            course_dir = self.state.course_dir(cfg, body.get("course") or "")
            mdir = body.get("dir") or ""
            if not _MODULE_DIR.match(mdir):
                return self._error(f"bad module dir: {mdir!r}")
            path = course_dir / mdir
        elif target == "file":
            q = {"course": [body.get("course") or ""], "dir": [body.get("dir") or ""],
                 "kind": [body.get("kind") or ""]}
            path, _ = self._artifact_path(q)
        else:
            return self._error(f"unknown target: {target!r}")
        if not Path(path).exists():
            return self._error(f"not found: {path}", 404)
        if hasattr(os, "startfile"):
            os.startfile(str(path))  # noqa: S606 - local desktop integration
        else:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, str(path)])
        self._json({"ok": True})

    def _post_config(self, body: dict) -> None:
        updates = body.get("updates")
        if not isinstance(updates, dict) or not updates:
            return self._error("no updates given")
        if self.state.model_override and "llm.model" in updates:
            return self._error(
                f"model is pinned to '{self.state.model_override}' by the --model launch flag"
            )
        try:
            apply_settings(self.state.config_path, updates)
        except ConfigEditError as e:
            return self._error(str(e))
        self._get_config()


def run(
    config_path: Path | str | None = None,
    port: int = 8765,
    open_browser: bool = True,
    model: str | None = None,
) -> None:
    if config_path is None:
        config_path = Path.cwd() / DEFAULT_CONFIG_NAME
    state = AppState(Path(config_path), model_override=model)
    state.cfg()  # fail fast on a broken config

    handler = type("BoundHandler", (Handler,), {"state": state})
    server = None
    bound_port = port
    last_err: OSError | None = None
    for candidate in range(port, port + 10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            bound_port = candidate
            break
        except OSError as e:
            last_err = e
    if server is None:
        raise SystemExit(f"Could not bind a local port starting at {port}: {last_err}")

    url = f"http://127.0.0.1:{bound_port}/"
    print(f"Capella Course Podcast Generator GUI: {url}")
    print("Local only (127.0.0.1). Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.4, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
