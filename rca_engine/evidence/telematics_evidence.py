"""轨B 证据适配器：车联网三域日志（MQTT / Logcat / CAN）对齐与降噪。

修复记录（对应 P1-1）：
1. 每次 load() 重置状态 —— 消除事件表跨调用污染；
2. 三个解析器统一异常计数（不再静默 pass），坏数据可见可查；
3. Logcat 时间戳改正则解析（兼容任意列宽），内容截取与格式解耦；
4. 增加 AlignStats 统计输出 —— 降噪数字可量化、可上 UI；
5. 支持失败时间窗口切片 —— 只把案发窗口交给 LLM，而非整段日志；
6. 黑名单从 config 注入，不再硬编码。
"""
import csv
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from .base import AlignStats, LogEvent

# Logcat 行首时间：MM-DD HH:MM:SS.mmm（毫秒，真实设备常有列宽差异）
_LOGCAT_TS_RE = re.compile(r"^(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})")


class TelematicsEvidenceParser:
    """确定性数据管道：解析 -> 全局归并排序 -> 规则降噪 -> 窗口切片 -> 统计。"""

    def __init__(self, data_dir, ignore_keywords: Optional[Tuple[str, ...]] = None,
                 current_year: Optional[int] = None):
        self.data_dir = Path(data_dir)
        self.ignore_keywords = tuple(ignore_keywords or ())
        self.current_year = current_year or datetime.now().year
        self._events: List[LogEvent] = []
        self.stats = AlignStats()

    # ---------------- 解析（各源独立，异常计数不吞） ----------------
    def _parse_mqtt(self) -> None:
        path = self.data_dir / "mqtt_cloud.log"
        if not path.exists():
            return
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    ts = datetime.fromisoformat(data["timestamp"])
                    self._events.append(LogEvent(ts=ts, source="CLOUD_MQTT", content=line.strip()))
                    self.stats.parsed += 1
                except Exception as e:
                    self._bump_error("mqtt", line, e)

    def _parse_logcat(self) -> None:
        path = self.data_dir / "tbox_logcat.txt"
        if not path.exists():
            return
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                m = _LOGCAT_TS_RE.match(line)
                if not m:
                    self._bump_error("logcat", line, "时间戳格式不匹配")
                    continue
                try:
                    ts = datetime.strptime(f"{self.current_year}-{m.group(1)}",
                                           "%Y-%m-%d %H:%M:%S.%f")
                    # 时间戳之后即为正文（列宽无关）
                    content = line[m.end():].strip()
                    self._events.append(LogEvent(ts=ts, source="TBOX_LOGCAT", content=content))
                    self.stats.parsed += 1
                except Exception as e:
                    self._bump_error("logcat", line, e)

    def _parse_can(self) -> None:
        path = self.data_dir / "can_trace.csv"
        if not path.exists():
            return
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    ts = datetime.fromtimestamp(float(row["Timestamp"]))
                    content = (f"ID: {row.get('ID', '?')} | Dir: {row.get('Dir', '?')} "
                               f"| DLC: {row.get('DLC', '?')} | Data: {row.get('Data', '?')}")
                    self._events.append(LogEvent(ts=ts, source="CAN_BUS", content=content))
                    self.stats.parsed += 1
                except Exception as e:
                    self._bump_error("can", row, e)

    def _bump_error(self, source: str, raw, err) -> None:
        self.stats.parse_errors[source] = self.stats.parse_errors.get(source, 0) + 1

    # ---------------- 对外接口 ----------------
    def load(self) -> List[LogEvent]:
        """解析全部源文件。每次调用重置状态，杜绝跨调用污染。"""
        self._events = []
        self.stats = AlignStats()
        self._parse_mqtt()
        self._parse_logcat()
        self._parse_can()
        return self._events

    def slice_window(self, events: List[LogEvent],
                     t0: Optional[datetime] = None,
                     t1: Optional[datetime] = None) -> List[LogEvent]:
        """按失败时间窗口切片（含 5 秒前置缓冲，保留上下文因果链）。"""
        if t0 is not None:
            t0 = t0 - timedelta(seconds=5)
        if t1 is not None:
            t1 = t1 + timedelta(seconds=5)
        out = []
        for e in events:
            if t0 is not None and e.ts < t0:
                continue
            if t1 is not None and e.ts > t1:
                continue
            out.append(e)
        return out

    def align(self, t0: Optional[datetime] = None, t1: Optional[datetime] = None,
              limit: int = 15000) -> Tuple[str, AlignStats]:
        """全流程：解析 -> 全局绝对时间归并排序 -> 规则降噪 -> 窗口切片 -> 格式化。"""
        events = self.load()
        events.sort(key=lambda e: e.ts)
        if t0 is not None or t1 is not None:
            events = self.slice_window(events, t0, t1)

        output: List[str] = []
        for e in events:
            if any(kw in e.content for kw in self.ignore_keywords):
                self.stats.dropped_noise += 1
                continue
            self.stats.kept += 1
            self.stats.kept_by_source[e.source] = self.stats.kept_by_source.get(e.source, 0) + 1
            ts_str = e.ts.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            output.append(f"[{ts_str}] [{e.source}] {e.content}")
            if len(output) >= limit:
                break
        return "\n".join(output), self.stats
