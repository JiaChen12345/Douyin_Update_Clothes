"""Validated and atomic storage for marketplace recordings."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from recording_models import RecordingProfile

PROFILE_FILES = {
    ("vvic", "tshirt"): "vvic_tshirt.json",
    ("vvic", "knitwear"): "vvic_knitwear.json",
    ("zwd17", "tshirt"): "zwd17_tshirt.json",
    ("zwd17", "knitwear"): "zwd17_knitwear.json",
}


@dataclass
class RecordingStatus:
    site: str
    category: str
    exists: bool
    compatible: bool
    recorded_at: str = ""
    path: str = ""
    error: str = ""


class RecordingStore:
    def __init__(self, bundled_dir: Path, user_dir: Path | None = None):
        self.bundled_dir = Path(bundled_dir)
        self.user_dir = Path(user_dir) if user_dir else self.bundled_dir

    @staticmethod
    def filename(site: str, category: str) -> str:
        try:
            return PROFILE_FILES[(site, category)]
        except KeyError as exc:
            raise ValueError(f"不支持的录制组合：{site}/{category}") from exc

    def paths(self, site: str, category: str) -> list[Path]:
        name = self.filename(site, category)
        candidates = [self.user_dir / name]
        bundled = self.bundled_dir / name
        if bundled != candidates[0]:
            candidates.append(bundled)
        return candidates

    def load(self, site: str, category: str) -> RecordingProfile:
        errors: list[str] = []
        for path in self.paths(site, category):
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                profile = RecordingProfile.from_dict(data)
                if profile.site != site or profile.category != category:
                    raise ValueError("录制文件组合与文件名不一致")
                self._normalize_upload_bindings(profile)
                return profile
            except Exception as exc:
                errors.append(f"{path}: {exc}")
        if errors:
            raise ValueError("；".join(errors))
        raise FileNotFoundError(f"缺少录制文件：{self.filename(site, category)}")

    def save(self, profile: RecordingProfile) -> Path:
        data = profile.to_dict()
        self.user_dir.mkdir(parents=True, exist_ok=True)
        target = self.user_dir / self.filename(profile.site, profile.category)
        temp = target.with_suffix(target.suffix + ".tmp")
        try:
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            check = RecordingProfile.from_dict(json.loads(temp.read_text(encoding="utf-8")))
            if check.site != profile.site or check.category != profile.category:
                raise ValueError("临时录制校验失败")
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        return target

    def status(self, site: str, category: str) -> RecordingStatus:
        existing = next((path for path in self.paths(site, category) if path.is_file()), None)
        if not existing:
            return RecordingStatus(site, category, False, False)
        try:
            profile = self.load(site, category)
            return RecordingStatus(site, category, True, True, profile.recorded_at, str(existing))
        except Exception as exc:
            return RecordingStatus(site, category, True, False, path_time(existing), str(existing), str(exc))

    def list_statuses(self) -> list[RecordingStatus]:
        return [self.status(site, category) for site, category in PROFILE_FILES]

    @staticmethod
    def _normalize_upload_bindings(profile: RecordingProfile) -> None:
        """Migrate early recordings that captured fake file paths or defaulted details to main images."""
        normalized = []
        for step in profile.steps:
            if step.action == "fill" and step.locator.attributes.get("type") == "file":
                continue
            if step.action == "upload":
                context = " ".join([step.name, step.locator.label, step.locator.name, step.locator.text] +
                                   [str(item.get("text", "")) for item in step.locator.ancestors])
                inferred = _infer_upload_variable(context)
                if inferred:
                    step.variable = inferred
                step.value = None
                step.critical = step.variable == "main_images"
            normalized.append(step)
        profile.steps = normalized


def path_time(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")


def _infer_upload_variable(context: str) -> str:
    text = "".join(str(context or "").split()).lower()
    if any(word in text for word in ("详情信息", "商品详情", "快捷生成详情页", "详情图")):
        return "detail_images"
    if any(word in text for word in ("上传吊牌", "吊牌图", "洗水标", "耐久性标签")):
        return "wash_label"
    if any(word in text for word in ("主图视频", "上传视频")) or "video" in text:
        return "videos"
    if any(word in text for word in ("1:1主图", "商品主图", "封面图", "白底图", "上传图片")):
        return "main_images"
    return ""
