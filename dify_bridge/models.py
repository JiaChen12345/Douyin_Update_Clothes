from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


STAGES = (
    ("prepare", "读取与下载商品素材"), ("open_creation", "打开抖店商品创建"),
    ("upload_main", "上传主图"), ("generate", "确认官方 AI 生成"),
    ("wait_ai", "等待官方 AI 生成完成"), ("upload_video", "上传主图视频"),
    ("wash_label", "上传水洗标/吊牌图"), ("attributes", "选择适用年龄"),
    ("shipping", "填写价格库存选项"), ("stock", "批量设置库存"),
    ("sizes", "选择尺码大小"), ("size_remarks", "填写尺码备注"),
    ("size_table", "填写尺码表"), ("save_draft", "保存草稿并验证"),
)
TERMINAL = {"SUCCESS", "FAILED", "CANCELLED", "INTERRUPTED"}


class NeedsHuman(RuntimeError):
    """Pause an existing step without inventing a successful browser outcome."""


class Region(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    normalized: bool = False

    @model_validator(mode="after")
    def valid_region(self):
        if self.normalized and (self.x + self.width > 1 or self.y + self.height > 1):
            raise ValueError("归一化区域必须在0到1的范围内")
        return self


class CreateTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=120, pattern=r"^[a-zA-Z0-9_-]+$")
    platform: str = "抖店"
    mode: Literal["demo", "live"] = "demo"
    source_url: str = Field(default="", max_length=3000)
    save_directory: str = Field(default="", max_length=1000)
    vision_enabled: bool = False
    roi: Region | None = None


class AdvanceTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: str = Field(min_length=1, max_length=40)


class VisionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    screenshot_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    found: StrictBool
    target: str = Field(min_length=1, max_length=500)
    x: float | None = None
    y: float | None = None
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def valid_coordinates(self):
        if self.found and (self.x is None or self.y is None or
                           not math.isfinite(self.x) or not math.isfinite(self.y)):
            raise ValueError("找到目标时必须提供有限的坐标")
        return self
