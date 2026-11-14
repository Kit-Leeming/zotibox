"""Bounded, local diagnostics. Record classifications, never raw exception text."""
from datetime import datetime, timezone
import json
import threading
import traceback
from pathlib import Path


def error_fields(error, *, frames=False):
    chain, seen, pending = [], set(), [error]
    while pending and len(chain) < 8:
        exc = pending.pop(0)
        if not isinstance(exc, BaseException) or id(exc) in seen:
            continue
        seen.add(id(exc))
        entry = {"type": type(exc).__name__}
        for key in ("errno", "winerror", "exit_code"):
            value = getattr(exc, key, None)
            if isinstance(value, int):
                entry[key] = value
        if frames:
            entry["frames"] = [f"{Path(f.filename).name}:{f.lineno}:{f.name}"
                               for f in traceback.extract_tb(exc.__traceback__, limit=-5)]
        chain.append(entry)
        pending.extend((exc.__cause__, exc.__context__, *exc.args))
    return chain


class Diagnostics:
    LIMIT = 64 * 1024

    def __init__(self, workspace, version, enabled=True):
        self.path = workspace / ".local" / "diagnostics.jsonl"
        self.version = version
        self.enabled = enabled
        self.lock = threading.RLock()

    def record(self, operation, outcome, *, elapsed_ms=None, error=None,
               frames=False, status=None):
        # Operation and outcome are fixed caller labels. No request data or UI messages.
        with self.lock:
            if not self.enabled:
                return
            row = {"utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "version": self.version, "operation": operation, "outcome": outcome}
            if elapsed_ms is not None:
                row["ms"] = elapsed_ms
            if status is not None:
                row["http"] = status
            if error is not None:
                row["errors"] = error_fields(error, frames=frames)
            try:
                payload = (json.dumps(row) + "\n").encode("ascii")
                self.path.parent.mkdir(exist_ok=True)
                if self.path.exists() and self.path.stat().st_size + len(payload) > self.LIMIT:
                    self.path.replace(self.path.with_suffix(".jsonl.1"))
                with self.path.open("ab") as stream:
                    stream.write(payload)
            except OSError:
                pass

    def report(self):
        with self.lock:
            parts = []
            for path in (self.path.with_suffix(".jsonl.1"), self.path):
                try:
                    with path.open("rb") as stream:
                        stream.seek(max(0, path.stat().st_size - self.LIMIT))
                        parts.append(stream.read(self.LIMIT).decode("utf-8", errors="replace"))
                except OSError:
                    pass
            return "".join(parts)

    def clear(self):
        with self.lock:
            for path in (self.path, self.path.with_suffix(".jsonl.1")):
                path.unlink(missing_ok=True)
