"""Versioned data models for recorded marketplace draft workflows."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

FORMAT_VERSION = 1
SITES = {"vvic", "zwd17"}
CATEGORIES = {"tshirt", "knitwear"}
ACTIONS = {"click", "fill", "select", "scroll", "upload", "navigate", "wait", "save_draft"}


@dataclass
class LocatorSpec:
    role: str = ""
    name: str = ""
    text: str = ""
    label: str = ""
    attributes: dict[str, str] = field(default_factory=dict)
    ancestors: list[dict[str, str]] = field(default_factory=list)
    x_ratio: float | None = None
    y_ratio: float | None = None


@dataclass
class ValidationSpec:
    kind: str = "none"
    value: Any = None
    timeout_ms: int = 15000


@dataclass
class RecordingStep:
    id: str
    name: str
    action: str
    locator: LocatorSpec = field(default_factory=LocatorSpec)
    value: Any = None
    variable: str = ""
    critical: bool = False
    retries: int = 2
    timeout_ms: int = 15000
    validation: ValidationSpec = field(default_factory=ValidationSpec)

    def validate(self) -> None:
        if not self.id or not self.name:
            raise ValueError("录制步骤缺少编号或名称")
        if self.action not in ACTIONS:
            raise ValueError(f"不支持的录制动作：{self.action}")
        if self.retries < 1 or self.timeout_ms < 1:
            raise ValueError(f"录制步骤参数无效：{self.id}")


@dataclass
class RecordingProfile:
    site: str
    category: str
    steps: list[RecordingStep]
    format_version: int = FORMAT_VERSION
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    viewport: dict[str, int] = field(default_factory=dict)
    page_markers: list[str] = field(default_factory=list)
    checksum: str = ""

    def validate(self, require_complete: bool = True) -> None:
        if self.format_version != FORMAT_VERSION:
            raise ValueError(f"录制格式版本不兼容：{self.format_version}")
        if self.site not in SITES or self.category not in CATEGORIES:
            raise ValueError(f"录制网站或类别无效：{self.site}/{self.category}")
        if not self.steps:
            raise ValueError("录制文件没有操作步骤")
        seen: set[str] = set()
        for step in self.steps:
            step.validate()
            if step.id in seen:
                raise ValueError(f"录制步骤编号重复：{step.id}")
            seen.add(step.id)
        if require_complete:
            actions = {step.action for step in self.steps}
            if "save_draft" not in actions:
                raise ValueError("录制流程缺少保存草稿步骤")
            if not any(step.action == "upload" and step.variable == "main_images" for step in self.steps):
                raise ValueError("录制流程缺少主图上传步骤")

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("checksum", None)
        return data

    def calculate_checksum(self) -> str:
        raw = json.dumps(self.payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        data = self.payload()
        data["checksum"] = self.calculate_checksum()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RecordingProfile":
        steps = []
        for item in data.get("steps", []):
            item = dict(item)
            item["locator"] = LocatorSpec(**item.get("locator", {}))
            item["validation"] = ValidationSpec(**item.get("validation", {}))
            steps.append(RecordingStep(**item))
        profile = cls(
            site=data.get("site", ""), category=data.get("category", ""), steps=steps,
            format_version=data.get("format_version", 0), recorded_at=data.get("recorded_at", ""),
            viewport=dict(data.get("viewport", {})), page_markers=list(data.get("page_markers", [])),
            checksum=data.get("checksum", ""),
        )
        profile.validate()
        if not profile.checksum or profile.checksum != profile.calculate_checksum():
            raise ValueError("录制文件完整性校验失败")
        return profile


@dataclass
class StepResult:
    step_id: str
    success: bool
    strategy: str = ""
    reason: str = ""
    skipped: bool = False


@dataclass
class ReplayResult:
    success: bool
    draft_saved: bool
    steps: list[StepResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failed_step: str = ""
    failure_reason: str = ""
