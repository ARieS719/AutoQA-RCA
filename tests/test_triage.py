"""失败分诊离线单测：证据门 —— 轨B 绝不无条件启动。"""
from pathlib import Path

from incident_bridge.evidence_collector import Incident
from incident_bridge.triage import Triage

GATE_PATTERNS = (r"(?i)\bMQTT\b", r"(?i)\bCanHal\b", r"(?i)timeout", r"(?i)超时")


def _write_logcat(base: Path, content: str) -> None:
    base.mkdir(parents=True, exist_ok=True)
    (base / "tbox_logcat.txt").write_text(content, encoding="utf-8")


def test_telematics_case_goes_track_b_only(tmp_path):
    incident = Incident(case_id="x", title="t", domain="telematics",
                        base_dir=tmp_path)
    plan = Triage(GATE_PATTERNS).decide(incident)
    assert plan.telematics is True and plan.ui is False


def test_mixed_case_goes_both(tmp_path):
    incident = Incident(case_id="x", title="t", domain="mixed",
                        link_dependency=["cloud", "tbox", "can"], base_dir=tmp_path)
    plan = Triage(GATE_PATTERNS).decide(incident)
    assert plan.telematics is True and plan.ui is True


def test_pure_ui_case_gate_blocks_track_b(tmp_path):
    _write_logcat(tmp_path, "10-04 18:00:01.000  4321  4321 E AndroidRuntime: FATAL EXCEPTION\n"
                            "10-04 18:00:01.000  1000  1050 I ActivityManager: Process has died\n")
    incident = Incident(case_id="x", title="t", domain="ui", base_dir=tmp_path)
    plan = Triage(GATE_PATTERNS).decide(incident)
    assert plan.ui is True and plan.telematics is False
    assert any("拦截" in r for r in plan.reasons)


def test_ui_case_with_link_signature_opens_track_b(tmp_path):
    # 纯 UI 用例，但失败窗口日志出现 CanHal 异常 -> 证据门放行轨B 补充取证
    _write_logcat(tmp_path, "10-04 18:00:01.000  1011  2046 E CanHal: CAN TX Timeout! Buffer full.\n")
    incident = Incident(case_id="x", title="t", domain="ui", base_dir=tmp_path)
    plan = Triage(GATE_PATTERNS).decide(incident)
    assert plan.ui is True and plan.telematics is True
    assert any("签名" in r for r in plan.reasons)


def test_ui_case_with_meta_dependency_opens_track_b(tmp_path):
    _write_logcat(tmp_path, "nothing suspicious here\n")
    incident = Incident(case_id="x", title="t", domain="ui",
                        link_dependency=["tbox"], base_dir=tmp_path)
    plan = Triage(GATE_PATTERNS).decide(incident)
    assert plan.telematics is True
