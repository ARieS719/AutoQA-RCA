"""统一事件模型 Incident 与证据采集器。

桥接层只负责"采集 + 打包"，与业务域无关：
- 演示/离线路径：从故障注入案例目录加载（case_meta.json + 证据文件）；
- 真机路径：从 UI Agent 的 step_recorder 运行目录收集（截图/步迹/logcat 转储）。
"""
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class Incident:
    """一次测试失败事件（统一事件模型）。"""
    case_id: str
    title: str
    domain: str                       # "ui" | "telematics" | "mixed"
    link_dependency: List[str] = field(default_factory=list)   # ["cloud","tbox","can"]
    time_window: Optional[Tuple[datetime, datetime]] = None     # (t0, t1) 失败时间窗
    base_dir: Path = Path(".")
    symptom: str = ""                 # 失败现象描述（供报告"现象"章节）
    meta: dict = field(default_factory=dict)
    allow_evidence_gate: bool = True  # 证据门是否参与分诊：
                                      # 自家 runner 产物=False（元数据说了算，logcat 噪音不算数）；
                                      # 来源不明的离线案例=True（用日志签名兜底）


class EvidenceCollector:
    @staticmethod
    def load_from_case_dir(case_dir) -> Incident:
        """从案例目录加载（演示/离线路径）。"""
        base = Path(case_dir)
        meta_path = base / "case_meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        else:
            meta = {}
        window = None
        if meta.get("time_window"):
            try:
                window = (datetime.fromisoformat(meta["time_window"][0]),
                          datetime.fromisoformat(meta["time_window"][1]))
            except Exception:
                window = None
        return Incident(
            case_id=meta.get("case_id", base.name),
            title=meta.get("title", base.name),
            domain=meta.get("domain", "ui"),
            link_dependency=list(meta.get("link_dependency", [])),
            time_window=window,
            base_dir=base,
            symptom=meta.get("symptom", ""),
            meta=meta,
        )

    @staticmethod
    def collect_from_run(run_dir) -> Optional[Incident]:
        """从 step_recorder 的运行目录收集（真机路径）。

        run_dir 结构：
            incident.json  {case_id, title, domain, link_dependency, started_at, finished_at, success}
            steps.jsonl    执行步迹
            step_*.png     逐步截图
            logcat_dump.txt ADB 转储的设备日志（含失败时间窗）
        """
        base = Path(run_dir)
        inc_path = base / "incident.json"
        if not inc_path.exists():
            return None
        data = json.loads(inc_path.read_text(encoding="utf-8"))
        window = None
        try:
            window = (datetime.fromisoformat(data["started_at"]),
                      datetime.fromisoformat(data["finished_at"]))
        except Exception:
            window = None
        return Incident(
            case_id=data.get("case_id", base.name),
            title=data.get("title", "真机测试失败"),
            domain=data.get("domain", "ui"),
            link_dependency=list(data.get("link_dependency", [])),
            time_window=window,
            base_dir=base,
            symptom=data.get("symptom", "UI 自动化用例执行失败"),
            meta=data,
            # 自家 runner 的用例域是测试人员声明的，logcat 里的车联噪音不得越权开轨B
            allow_evidence_gate=False,
        )

    @staticmethod
    def list_demo_cases(data_dir) -> List[Path]:
        """列出 demo_cases 下全部注入案例目录。"""
        root = Path(data_dir) / "demo_cases"
        if not root.exists():
            return []
        return sorted(p for p in root.iterdir() if p.is_dir() and (p / "case_meta.json").exists())
