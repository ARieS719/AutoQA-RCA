"""证据层公共数据结构。

统一事件模型 Incident 见 incident_bridge/evidence_collector.py；
本模块只承载日志事件的通用表示与对齐统计。
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict


@dataclass
class LogEvent:
    """一条跨域归一后的日志事件。"""
    ts: datetime
    source: str          # CLOUD_MQTT / TBOX_LOGCAT / CAN_BUS
    content: str


@dataclass
class AlignStats:
    """确定性数据管道的量化统计（降噪卖点的数字出口）。"""
    parsed: int = 0
    dropped_noise: int = 0
    kept: int = 0
    parse_errors: Dict[str, int] = field(default_factory=dict)   # source -> 解析失败行数
    kept_by_source: Dict[str, int] = field(default_factory=dict)  # source -> 保留行数

    @property
    def noise_removal_ratio(self) -> float:
        """降噪率（精确浮点值；展示层自行格式化，不做四舍五入）。"""
        if self.parsed == 0:
            return 0.0
        return self.dropped_noise / self.parsed

    @property
    def compression_ratio(self) -> float:
        """上下文压缩比（精确浮点值；展示层自行格式化）。"""
        if self.kept == 0:
            return 0.0
        return self.parsed / self.kept

    def summary(self) -> str:
        err = " ".join(f"{k}={v}" for k, v in self.parse_errors.items()) or "无"
        return (
            f"解析 {self.parsed} 行 -> 剔除噪音 {self.dropped_noise} 行 "
            f"(降噪 {self.noise_removal_ratio:.1%}) -> 保留核心上下文 {self.kept} 行 "
            f"(压缩 {self.compression_ratio}x)；解析失败: {err}；"
            f"分源保留: {self.kept_by_source or '无'}"
        )
