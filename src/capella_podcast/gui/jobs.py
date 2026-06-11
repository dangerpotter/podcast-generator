"""Background job execution for the GUI.

A single worker thread runs jobs sequentially: the LLM and TTS engines are
heavy, so parallel runs would thrash RAM. While a job runs, Python-level
stdout/stderr are teed into an in-memory log the frontend polls. Cancellation
is cooperative and takes effect between items, not mid-generation.
"""

from __future__ import annotations

import io
import itertools
import queue
import sys
import threading
import traceback
from datetime import datetime, timezone
from typing import Callable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobCancelled(Exception):
    """Raised inside a job when the user requested cancellation."""


class Job:
    _ids = itertools.count(1)

    def __init__(self, title: str, fn: Callable[["Job"], None]):
        self.id = next(Job._ids)
        self.title = title
        self._fn = fn
        self.status = "queued"  # queued | running | done | error | cancelled
        self.error: str | None = None
        self.created_at = _now()
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.progress: dict | None = None  # {"done", "total", "label"}
        self._log: list[str] = []
        self._buf = ""
        self._lock = threading.Lock()
        self._cancel = threading.Event()

    # -- log capture ------------------------------------------------------
    def write(self, s: str) -> None:
        with self._lock:
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                self._log.append(line.rstrip("\r"))

    def log(self, line: str) -> None:
        self.write(line + "\n")

    def log_since(self, index: int) -> tuple[list[str], int]:
        with self._lock:
            index = max(0, min(index, len(self._log)))
            return self._log[index:], len(self._log)

    # -- progress / cancellation ------------------------------------------
    def set_progress(self, done: int, total: int, label: str) -> None:
        self.progress = {"done": done, "total": total, "label": label}

    def request_cancel(self) -> None:
        self._cancel.set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def check_cancel(self) -> None:
        if self._cancel.is_set():
            raise JobCancelled()

    def summary(self) -> dict:
        with self._lock:
            log_len = len(self._log)
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "progress": self.progress,
            "log_length": log_len,
            "cancel_requested": self.cancel_requested,
        }


class _Tee(io.TextIOBase):
    """Mirror writes to the real stream and into the job log."""

    def __init__(self, job: Job, real):
        self.job = job
        self.real = real

    def write(self, s: str) -> int:
        try:
            self.real.write(s)
            self.real.flush()
        except Exception:
            pass
        self.job.write(s)
        return len(s)

    def flush(self) -> None:
        try:
            self.real.flush()
        except Exception:
            pass


class JobManager:
    """One worker thread, FIFO queue, bounded job history."""

    HISTORY = 50

    def __init__(self):
        self._queue: queue.Queue[Job] = queue.Queue()
        self._lock = threading.Lock()
        self.jobs: list[Job] = []
        self.current: Job | None = None
        self._worker = threading.Thread(target=self._run, daemon=True, name="gui-job-worker")
        self._worker.start()

    def submit(self, title: str, fn: Callable[[Job], None]) -> Job:
        job = Job(title, fn)
        with self._lock:
            self.jobs.append(job)
            del self.jobs[: -self.HISTORY]
        self._queue.put(job)
        return job

    def busy(self) -> bool:
        with self._lock:
            return any(j.status in ("queued", "running") for j in self.jobs)

    def get(self, job_id: int) -> Job | None:
        with self._lock:
            return next((j for j in self.jobs if j.id == job_id), None)

    def latest(self) -> Job | None:
        with self._lock:
            return self.jobs[-1] if self.jobs else None

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            if job.cancel_requested:
                job.status = "cancelled"
                job.finished_at = _now()
                continue
            with self._lock:
                self.current = job
            job.status = "running"
            job.started_at = _now()
            real_out, real_err = sys.stdout, sys.stderr
            sys.stdout, sys.stderr = _Tee(job, real_out), _Tee(job, real_err)
            try:
                job._fn(job)
                job.status = "done"
            except JobCancelled:
                job.status = "cancelled"
                job.log("Cancelled by user.")
            except Exception as e:  # noqa: BLE001 - job boundary
                job.status = "error"
                job.error = str(e) or type(e).__name__
                job.log(traceback.format_exc())
            finally:
                sys.stdout, sys.stderr = real_out, real_err
                job.finished_at = _now()
                with self._lock:
                    self.current = None
