"""失败分诊（Triage）：决定本次失败走哪条轨。

核心原则（对应口径问题的最终决策）：
- 轨B 绝不因"轨A 没查到"而启动，只由【证据门】触发：
  (a) 用例元数据声明了链路依赖（domain=mixed / link_dependency 非空）；
  (b) 失败窗口日志出现链路异常签名（MQTT/CanHal/socket/超时等，正则可解释）。
- 纯 UI 失败（domain=ui 且无签名命中）永远只走轨A，不碰一条 CAN 报文。
"""
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from .evidence_collector import Incident

logger = logging.getLogger(__name__)


@dataclass
class TrackPlan:
    ui: bool = True
    telematics: bool = False
    reasons: List[str] = field(default_factory=list)


class Triage:
    def __init__(self, evidence_gate_patterns=None):
        self.patterns = [re.compile(p) for p in (evidence_gate_patterns or [])]

    # ---------------- 证据门：签名检测 ----------------
    def _logcat_signature_hit(self, case_dir: Path) -> List[str]:
        path = Path(case_dir) / "tbox_logcat.txt"
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits = []
        for pat in self.patterns:
            if pat.search(text):
                hits.append(pat.pattern)
        return hits

    # ---------------- 分诊主逻辑 ----------------
    def decide(self, incident: Incident) -> TrackPlan:
        plan = TrackPlan()
        domain = (incident.domain or "ui").lower()

        if domain == "telematics":
            # 纯链路案例（无 UI 侧证据）：只走轨B
            plan.ui = False
            plan.telematics = True
            plan.reasons.append("用例域=telematics（纯车联网链路故障）")
            return plan

        if domain == "mixed":
            # 声明了链路依赖的 UI 用例：从声明起即双轨并行取证
            plan.ui = True
            plan.telematics = True
            plan.reasons.append("用例元数据声明链路依赖（domain=mixed）")
            if incident.link_dependency:
                plan.reasons.append("link_dependency=" + ",".join(incident.link_dependency))
            return plan

        # domain == "ui"：默认只走轨A；仅当证据门命中才补充轨B
        plan.ui = True
        plan.telematics = False
        if incident.link_dependency:
            plan.telematics = True
            plan.reasons.append("用例元数据声明链路依赖: " + ",".join(incident.link_dependency))
        # 证据门只用于"来源不明"的案例；自家 runner 用例以元数据为准
        # （AAOS 等设备 logcat 天然充满 CanHal/T-BOX 噪音，会误开轨B 出幻觉结论）
        sig_hits = []
        if getattr(incident, "allow_evidence_gate", True):
            sig_hits = self._logcat_signature_hit(incident.base_dir)
        if sig_hits:
            plan.telematics = True
            plan.reasons.append("失败窗口日志命中链路异常签名: " + ", ".join(sorted(set(sig_hits))[:5]))
        if not plan.telematics:
            plan.reasons.append("纯 UI 域失败且无链路异常签名 —— 轨B 不启动（证据门拦截）")
        return plan
