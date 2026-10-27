"""轨B 对齐降噪的离线单测：排序 / 降噪 / 窗口切片 / 状态重置 / 异常计数。"""
import csv
import json
from datetime import datetime

from rca_engine.evidence.telematics_evidence import TelematicsEvidenceParser


def _make_case(tmp_path):
    base = tmp_path / "case"
    base.mkdir()
    (base / "mqtt_cloud.log").write_text("\n".join([
        json.dumps({"timestamp": "2026-10-04T18:00:03.000", "topic": "/app/req",
                    "payload": {}, "level": "INFO"}),
        json.dumps({"timestamp": "2026-10-04T18:00:01.000", "topic": "/vehicle/status",
                    "payload": {}, "level": "DEBUG"}),
    ]), encoding="utf-8")
    (base / "tbox_logcat.txt").write_text("\n".join([
        "10-04 18:00:02.500  1011  2045 I TBoxMqtt: Received cmd: unlock",
        "10-04 18:00:04.000  1000  1050 I ActivityManager: CPU usage normal, memory stable",
        "这条是坏数据没有时间戳",
    ]), encoding="utf-8")
    with open(base / "can_trace.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Timestamp", "ID", "Dir", "DLC", "Data"])
        w.writerow([f"{datetime(2026, 10, 4, 18, 0, 5).timestamp():.6f}", "0x3F2", "TX", "8", "01"])
        w.writerow([f"{datetime(2026, 10, 4, 18, 0, 0).timestamp():.6f}", "0x0C1", "RX", "8", "FF"])
    return base


def test_align_order_filter_stats_and_reset(tmp_path):
    base = _make_case(tmp_path)
    parser = TelematicsEvidenceParser(base, ignore_keywords=("ActivityManager",))

    text, stats = parser.align()
    lines = text.splitlines()
    # 时间全局升序：0x0C1(18:00:00) -> MQTT(01) -> logcat(02.5) -> MQTT(03) -> CAN(05)
    assert lines[0].startswith("[2026-10-04 18:00:00")
    assert lines[1].startswith("[2026-10-04 18:00:01")
    assert lines[2].startswith("[2026-10-04 18:00:02")
    # ActivityManager 行被黑名单剔除
    assert "ActivityManager" not in text
    # 统计：6 行解析成功，1 行噪音剔除，保留 5 行
    assert stats.parsed == 6
    assert stats.dropped_noise == 1
    assert stats.kept == 5
    assert stats.noise_removal_ratio == 1 / 6
    # 坏数据被计数而非静默吞掉
    assert stats.parse_errors.get("logcat") == 1

    # 二次调用不翻倍（状态重置修复验证）
    _, stats2 = parser.align()
    assert stats2.parsed == stats.parsed == 6
    assert stats2.kept == stats.kept == 5


def test_window_slicing(tmp_path):
    base = _make_case(tmp_path)
    parser = TelematicsEvidenceParser(base, ignore_keywords=("ActivityManager",))
    t0 = datetime(2026, 10, 4, 18, 0, 1)
    t1 = datetime(2026, 10, 4, 18, 0, 3)
    text, stats = parser.align(t0=t0, t1=t1)
    # slice_window 设计带 ±5s 缓冲（保留因果上下文），实际窗口 ≈ [17:59:56, 18:00:08]：
    # 含 CAN(00)、MQTT(01)、logcat(02.5)、MQTT(03)、CAN(05)；不含 ActivityManager(04) 噪音行
    assert stats.kept == 5
    assert "18:00:05" in text          # 后置缓冲内的 CAN 帧被保留
    assert "ActivityManager" not in text

    # 远离案发时间的窗口：±5s 缓冲也够不着任何事件
    text2, stats2 = parser.align(t0=datetime(2026, 10, 4, 17, 0, 0),
                                 t1=datetime(2026, 10, 4, 17, 0, 1))
    assert stats2.kept == 0
