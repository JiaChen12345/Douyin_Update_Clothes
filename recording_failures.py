"""Persistent consecutive replay failure tracking."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


class FailureTracker:
    def __init__(self, path: Path, threshold: int = 3):
        self.path = Path(path)
        self.threshold = threshold
        self.data = self._load()

    @staticmethod
    def key(site: str, category: str, step_id: str) -> str:
        return f"{site}:{category}:{step_id}"

    def _load(self) -> dict:
        if not self.path.is_file():
            return {"version": 1, "failures": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8-sig"))
            return value if isinstance(value.get("failures"), dict) else {"version": 1, "failures": {}}
        except Exception:
            return {"version": 1, "failures": {}}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)

    def success(self, site: str, category: str, step_id: str) -> None:
        self.data["failures"].pop(self.key(site, category, step_id), None)
        self._save()

    def failure(self, site: str, category: str, step_id: str, reason: str) -> dict | None:
        key = self.key(site, category, step_id)
        item = self.data["failures"].setdefault(key, {"count": 0, "notified": False})
        item.update({"count": int(item.get("count", 0)) + 1, "reason": reason,
                     "updated_at": datetime.now(timezone.utc).isoformat()})
        notify = item["count"] >= self.threshold and not item.get("notified", False)
        if notify:
            item["notified"] = True
        self._save()
        return {"site": site, "category": category, "step_id": step_id, **item} if notify else None

    def clear_profile(self, site: str, category: str) -> None:
        prefix = f"{site}:{category}:"
        self.data["failures"] = {key: value for key, value in self.data["failures"].items() if not key.startswith(prefix)}
        self._save()
