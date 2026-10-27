"""步骤证据记录器：把每一步的决策/截图/异常落盘为证据包。

这是"测→析"闭环的原料层：UI 测试失败后，incident_bridge
直接读取本目录（steps.jsonl + 截图 + logcat 转储 + incident.json），
无需二次采集即可进入分诊与双轨 RCA。
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class StepRecorder:
    def __init__(self, base_dir, case_id: str):
        self.base_dir = Path(base_dir)
        self.case_id = case_id
        self.run_dir: Optional[Path] = None
        self.started_at: Optional[datetime] = None
        self._steps = []

    def start(self, title: str, domain: str = "ui",
              link_dependency: Optional[list] = None,
              symptom: str = "") -> Path:
        self.started_at = datetime.now()
        self.run_dir = self.base_dir / "runs" / f"{self.case_id}_{self.started_at.strftime('%Y%m%d_%H%M%S')}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._steps = []
        meta = {
            "case_id": self.case_id,
            "title": title,
            "domain": domain,
            "link_dependency": list(link_dependency or []),
            "symptom": symptom,
            "started_at": self.started_at.isoformat(),
            "started": True,
        }
        (self.run_dir / "incident.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("证据运行目录已创建: %s", self.run_dir)
        return self.run_dir

    def record_step(self, step_no: int, action: str, target_id: str = "",
                    thought: str = "", anomaly: bool = False,
                    screenshot_path: Optional[str] = None) -> None:
        self._steps.append({
            "step": step_no,
            "action": action,
            "target_id": target_id,
            "thought": thought,
            "anomaly_handled": anomaly,
            "screenshot": screenshot_path,
            "ts": datetime.now().isoformat(),
        })

    def finish(self, success: bool, note: str = "") -> Path:
        """收口：写步迹文件 + 更新 incident.json（含失败时间窗）。"""
        if self.run_dir is None:
            raise RuntimeError("请先调用 start()")
        finished_at = datetime.now()
        with open(self.run_dir / "steps.jsonl", "a", encoding="utf-8") as f:
            for s in self._steps:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
        meta_path = self.run_dir / "incident.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta.update({
            "finished_at": finished_at.isoformat(),
            "success": success,
            "note": note,
            "started": False,
        })
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("证据包收口完成: %s (success=%s)", self.run_dir, success)
        return self.run_dir
