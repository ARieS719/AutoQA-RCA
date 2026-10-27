"""离线端到端测试：用假 LLM / 假 RAG 跑完整 LangGraph 双轨流水线。

不依赖网络与真机，验证：图拓扑、节点无空转、证据门路由、
Tool-Use 循环、双向仲裁（链路健康 -> 责任回摆座舱）、报告落盘。
"""
import csv
import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("langchain_core")

from incident_bridge.evidence_collector import Incident          # noqa: E402
from incident_bridge.triage import Triage                        # noqa: E402
from rca_engine.graph_pipeline import run_rca_pipeline           # noqa: E402
from rca_engine.rag_store import FakeRAGStore                    # noqa: E402
from rca_engine.report_writer import ReportWriter                # noqa: E402


class FakeResponse:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class FakeChatModel:
    """按提示词内容路由的假 LLM：与 LangGraph 节点执行顺序无关，保证测试稳健。"""

    def __init__(self, routes):
        # routes: [(匹配子串, 响应dict)], 按顺序命中
        self.routes = list(routes)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        text = str(messages)
        for key, resp in self.routes:
            if key in text:
                return FakeResponse(resp.get("content", ""), resp.get("tool_calls") or [])
        raise AssertionError(f"FakeChatModel 未匹配任何路由，消息前 200 字: {text[:200]}")


def _make_mixed_case(tmp_path):
    base = tmp_path / "case5"
    base.mkdir()
    (base / "case_meta.json").write_text(json.dumps({
        "case_id": "case_5_ui_but_link_ok", "title": "远程解锁显示失败但链路成功",
        "domain": "mixed", "link_dependency": ["cloud", "tbox", "can"],
        "symptom": "UI 显示失败", "time_window": None,
    }), encoding="utf-8")
    (base / "mqtt_cloud.log").write_text(
        '{"timestamp": "2026-10-04T18:00:01.000", "topic": "/app/req", "payload": {}, "level": "INFO"}\n'
        '{"timestamp": "2026-10-04T18:00:02.500", "topic": "/vehicle/tbox/status", '
        '"payload": {"status": "SUCCESS"}, "level": "INFO"}\n', encoding="utf-8")
    (base / "tbox_logcat.txt").write_text(
        "10-04 18:00:01.500  1011  2045 I TBoxMqtt: Received cmd: unlock\n"
        "10-04 18:00:02.000  1011  2046 I VehicleControl: Door unlock executed OK. BCM status: 00\n"
        "10-04 18:00:02.100  5566  5566 E AndroidRuntime: FATAL EXCEPTION: StatusSyncService\n"
        "10-04 18:00:02.100  5566  5566 E AndroidRuntime: "
        "java.lang.NullPointerException: statusPayload is null\n", encoding="utf-8")
    with open(base / "can_trace.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Timestamp", "ID", "Dir", "DLC", "Data"])
        w.writerow(["1764831601.700000", "0x3F2", "TX", "8", "01 00 00 00 00 00 00 00"])
        w.writerow(["1764831601.900000", "0x3F3", "RX", "8", "00 00 00 00 00 00 00 00"])
    (base / "ui_steps.jsonl").write_text("\n".join([
        json.dumps({"step": 1, "action": "TAP", "target_id": "2",
                    "thought": "点击车门解锁", "anomaly_handled": False}),
        json.dumps({"step": 2, "action": "FAIL", "target_id": "",
                    "thought": "界面显示执行失败", "anomaly_handled": False}),
    ]), encoding="utf-8")
    return base


def _llm_routes():
    """按提示词特征路由的响应表（顺序敏感：先匹配更具体的特征）。"""
    return [
        # 轨A
        ("座舱 UI 测试开发专家", {
            "content": "线索：座舱状态同步服务崩溃，链路日志显示指令已执行成功。"}),
        ("座舱 UI 缺陷裁判", {
            "content": json.dumps({"root_cause": "StatusSyncService 空指针崩溃",
                                   "responsibility": "座舱应用团队",
                                   "confidence": "high",
                                   "actions": ["状态推送回调增加空值防御"]})}),
        # 轨B
        ("车联网测试开发专家", {
            "content": "提纯：0x3F2 指令正常下发，0x3F3 返回成功码 00，云端推送 SUCCESS。"}),
        ("请总结以上解码结论", {
            "content": "解码总结：0x3F2 为远程解锁指令，链路应答正常。"}),
        ("工具返回", {
            "content": "", "tool_calls": []}),          # 第二轮：工具结果已附，停止调用
        ("调用 decode_can_protocol 工具", {
            "content": "", "tool_calls": [
                {"name": "decode_can_protocol", "args": {"can_id": "0x3F2"}}]}),
        ("汽车测试架构师", {
            "content": json.dumps({"root_cause": "链路层全部正常应答",
                                   "responsibility": "链路无责",
                                   "link_healthy": True,
                                   "confidence": "high",
                                   "actions": []})}),
    ]


def test_mixed_case_arbitrates_back_to_ui(tmp_path):
    base = _make_mixed_case(tmp_path)
    incident = Incident(case_id="case_5_ui_but_link_ok", title="t", domain="mixed",
                        link_dependency=["cloud", "tbox", "can"], base_dir=base)
    plan = Triage(()).decide(incident)
    assert plan.ui and plan.telematics

    rag = FakeRAGStore([
        {"id": "Jira-9002", "document": "座舱状态同步服务崩溃", "metadata": {"domain": "座舱"}},
        {"id": "Jira-8832", "document": "ECM 心跳风暴", "metadata": {"domain": "车联"}},
    ])
    writer = ReportWriter(tmp_path / "reports")
    result = run_rca_pipeline(incident, plan, llm=FakeChatModel(_llm_routes()),
                              rag=rag, writer=writer, use_graph=True, offline=False)

    # 双向仲裁：链路健康 -> 责任回摆座舱应用层
    assert result["arbitration"]["direction"] == "telematics->ui"
    assert result["final"]["final"]["responsibility"] == "座舱应用团队"
    assert result["final"]["final"]["root_cause"] == "StatusSyncService 空指针崩溃"
    # 对齐统计与报告落盘
    assert result["tlm_stats"]["kept"] >= 1
    assert Path(result["report_md_path"]).exists()


def test_ui_only_case_never_touches_telematics_track(tmp_path):
    base = tmp_path / "case1"
    base.mkdir()
    (base / "case_meta.json").write_text(json.dumps({
        "case_id": "case_1_ui_crash", "title": "音乐 App 闪退",
        "domain": "ui", "link_dependency": [], "symptom": "闪退",
    }), encoding="utf-8")
    (base / "tbox_logcat.txt").write_text(
        "10-04 18:00:01.000  4321  4321 E AndroidRuntime: FATAL EXCEPTION: main\n"
        "10-04 18:00:01.000  4321  4321 E AndroidRuntime: "
        "java.lang.ArrayIndexOutOfBoundsException\n", encoding="utf-8")
    (base / "ui_steps.jsonl").write_text(
        json.dumps({"step": 1, "action": "FAIL", "target_id": "",
                    "thought": "闪退", "anomaly_handled": False}), encoding="utf-8")

    incident = Incident(case_id="case_1_ui_crash", title="t", domain="ui", base_dir=base)
    plan = Triage(()).decide(incident)
    assert plan.telematics is False

    result = run_rca_pipeline(incident, plan,
                              llm=FakeChatModel([
                                  ("座舱 UI 测试开发专家",
                                   {"content": "线索：音频焦点数组越界"}),
                                  ("座舱 UI 缺陷裁判",
                                   {"content": json.dumps({
                                       "root_cause": "数组越界崩溃",
                                       "responsibility": "座舱应用团队",
                                       "confidence": "high",
                                       "actions": []})}),
                              ]),
                              rag=FakeRAGStore(), writer=None,
                              use_graph=True, offline=False)
    # 轨B 各节点全程空转：结果中不应出现任何车联产物
    assert "tlm_verdict" not in result
    assert result["final"]["final"]["responsibility"] == "座舱应用团队"
