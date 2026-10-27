"""评测计分逻辑离线单测（不依赖 LLM / 网络）。"""
from mock_data.truth_schema import build_truth
from rca_engine.evaluate import _hit, _rate, score_one


def _fake_result(triage_ui, triage_tlm, root_cause, responsibility,
                 rag_evidence, direction):
    return {"final": {
        "triage": {"ui": triage_ui, "telematics": triage_tlm, "reasons": []},
        "final": {"root_cause": root_cause, "responsibility": responsibility,
                  "confidence": "high", "rag_evidence": rag_evidence},
        "arbitration": {"note": "", "direction": direction},
    }}


TRUTH = build_truth(
    expected_track={"ui": True, "telematics": True},
    arbitration_direction="telematics->ui",
    root_cause_keywords=["NullPointerException", "空指针"],
    responsibility_keywords=["座舱应用"],
    rag_hit="Jira-9002",
)


def test_hit_keyword_matching():
    assert _hit("StatusSyncService 空指针崩溃", ["空指针"]) is True
    assert _hit("StatusSyncService 空指针崩溃", ["NullPointerException"]) is False
    assert _hit("", []) is True


def test_score_one_full_hit():
    row = score_one(_fake_result(True, True,
                                 "座舱状态同步服务空指针崩溃 (NullPointerException)",
                                 "座舱应用团队", "检索到 [Jira-9002]",
                                 "telematics->ui"), TRUTH, offline=False)
    assert row["triage"]["match"] is True
    assert row["root_cause"]["hit"] is True
    assert row["responsibility"]["hit"] is True
    assert row["rag"]["hit"] is True
    assert row["arbitration"]["match"] is True


def test_score_one_miss():
    row = score_one(_fake_result(True, False,
                                 "CAN 总线拥堵", "动力域", "未检索到", "single"),
                    TRUTH, offline=False)
    assert row["triage"]["match"] is False          # 轨B 被漏开
    assert row["root_cause"]["hit"] is False
    assert row["arbitration"]["match"] is False


def test_score_one_offline_skips_llm_metrics():
    row = score_one(_fake_result(True, True, "", "", "", "manual"), TRUTH, offline=True)
    assert row["triage"]["match"] is True
    assert row["root_cause"] == "skipped"
    assert row["arbitration"] == "skipped"


def test_rate_counts_match_and_hit():
    # 回归：triage 用 match 键，其余指标用 hit 键，聚合须两者都统计
    rows = [
        {"triage": {"match": True}, "root_cause": {"hit": True}},
        {"triage": {"match": False}, "root_cause": {"hit": False}},
        {"triage": {"match": True}, "root_cause": {"hit": True}},
    ]
    assert _rate(rows, "triage") == 2 / 3
    assert _rate(rows, "root_cause") == 2 / 3
