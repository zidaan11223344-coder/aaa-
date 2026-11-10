"""Small standalone JSON state helper used only by the Cricket module."""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")

def normalize(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())

class JsonState:
    def __init__(self, path: str | Path, default: Callable[[], Any], persist: Callable[[Path, Any], None] | None = None):
        self.path = Path(path)
        self.default = default
        self.persist = persist
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    @contextmanager
    def _lock(self):
        self.lock_path.touch(mode=0o600, exist_ok=True)
        with self.lock_path.open("r+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _read_unlocked(self):
        if not self.path.exists():
            return self.default()
        with self.path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _write_unlocked(self, value):
        fd, temp_name = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(value, f, ensure_ascii=False, indent=2, sort_keys=True)
                f.write("\n")
                f.flush(); os.fsync(f.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.path)
            os.chmod(self.path, 0o600)
            if self.persist:
                self.persist(self.path, value)
        finally:
            Path(temp_name).unlink(missing_ok=True)

    def load(self):
        with self._lock():
            return self._read_unlocked()

    def mutate(self, action: Callable[[Any], T]) -> T:
        with self._lock():
            value = self._read_unlocked()
            result = action(value)
            self._write_unlocked(value)
            return result
