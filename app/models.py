"""共享数据模型、常量与输入错误类型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

# 业务上限（题面约定）
MAX_STOPS = 6
MAX_POINTS = 40
MIN_DWELL_POINTS = 2

# 上传与文件限制
MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 单个 CSV 2 MiB
MAX_REQUEST_BYTES = 6 * 1024 * 1024  # multipart 总请求体上限

# 球面距离使用平均地球半径（米）
EARTH_RADIUS_M = 6_371_000.0

# 浮点边界容差，仅用于“恰好落在半径上”的稳定判断
DISTANCE_EPSILON_M = 1e-9


class InputError(Exception):
    """输入数据无法使用：文件格式、表头、数值、时刻顺序等。

    该异常表示“没有可交付的分析”：调用方应展示原因且不得输出任何事实。
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_input",
        details: Optional[list[str]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = list(details or [])

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True)
class Stop:
    """线路上的一个站点，index 为 0 基线路顺序。"""

    index: int
    name: str
    lat: float
    lon: float
    radius_m: float


@dataclass(frozen=True)
class Position:
    """一个原始定位点，index 为 0 基时间顺序。"""

    index: int
    time: datetime  # 带时区，统一为 UTC
    lat: float
    lon: float
    raw_time: str = ""


def utc_iso(dt: datetime) -> str:
    """输出明确的 UTC ISO-8601 字符串（以 Z 结尾）。"""

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    text = dt.astimezone(timezone.utc).isoformat()
    if text.endswith("+00:00"):
        text = text[:-6] + "Z"
    return text
