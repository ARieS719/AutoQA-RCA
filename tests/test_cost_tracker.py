"""成本计量离线单测：Token 估算与费用/节省计算。"""
from rca_engine.cost_tracker import CostTracker, TokenEstimator


def test_estimate_basics():
    assert TokenEstimator.estimate("") == 0
    assert TokenEstimator.estimate("hello world") >= 3      # 11 字符 / 3.5 ≈ 3
    assert TokenEstimator.estimate("中文测试") >= 2          # 4 字 × 0.65 ≈ 2.6


def test_tracker_accumulates():
    t = CostTracker(input_price_per_1m=2.0, output_price_per_1m=8.0)
    t.record("ui_purify", "你好世界 " * 100, "结论")
    t.record("ui_judge", "prompt text", "根因")
    assert t.calls == 2
    assert t.prompt_tokens > 0
    assert t.completion_tokens > 0
    assert t.by_node["ui_purify"]["calls"] == 1
    # 费用恒为正且量级合理（远小于 1 元）
    assert 0 < t.est_cost_cny() < 0.01


def test_noise_savings_math():
    t = CostTracker()
    s = t.noise_savings(dropped_noise=1261, avg_tokens_per_line=15.0)
    assert s["est_input_tokens_saved"] == int(1261 * 15.0)
    # 输入价 2 元/百万 token -> 18915/1e6*2 = 0.0378
    assert s["est_input_cost_saved_cny"] == 0.0378


def test_summary_shape():
    t = CostTracker()
    t.record("x", "hello")
    s = t.summary(savings={"noise_lines_filtered": 1})
    assert s["llm_calls"] == 1
    assert "noise_savings" in s
    assert "note" in s          # 诚实口径说明
