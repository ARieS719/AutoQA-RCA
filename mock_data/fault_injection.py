"""★ 错误注入演示集（Fault Injection Showcase）★

这是你点名的"专门放错误进去"的文件：每个案例目录里都**人为埋了一个故障**，
配套生成证据文件（日志/截图/步迹），并在 `ground_truth.md` 里封存答案卡。

玩法：
1. `python -m mock_data.fault_injection`        生成全部 5 个案例；
2. `python -m rca_engine.cli cases`             查看案例清单；
3. `python -m rca_engine.cli diagnose --case data_output/demo_cases/<案例>`  让 AI 诊断；
4. 先读 AI 的 RCA 报告，再打开该案例目录下的 `ground_truth.md` 对答案。

案例设计（故意安排的难度点）：
- case_1_ui_crash        轨A 专场：纯 UI 崩溃，但日志里埋了"设置启动成功"的干扰链路，
                         考验证据门是否拦住轨B（不应去查 CAN）；
- case_2_can_congestion  轨B：ECM 心跳风暴抢占 CAN 总线（历史 Jira-8832 旧病复发）；
- case_3_network_offline 轨B：车库弱网 MQTT 死锁（历史 Jira-9022）；
- case_4_hardware_fault  轨B：BCM 执行器硬件故障反馈 0xEE（历史 Jira-9155）；
- case_5_ui_but_link_ok  双轨仲裁专场：链路一切正常，但座舱 App 状态同步服务崩溃——
                         期望 AI 给出"链路无责，责任回摆座舱应用层"的仲裁结论；
- case_6_field_noisy    场域仿真：脏数据（截断行/带年份行/重复行/重复下发）+
                         未知诊断报文 0x7E8 洪泛，考验解析鲁棒性与未知 ID 诚实降级。
"""
import csv
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mock_data.generate_logs import generate_scenario  # noqa: E402
from mock_data.truth_schema import build_truth, write_truth  # noqa: E402

BASE = datetime(2026, 10, 4, 18, 0, 0)  # 固定基准时间，保证案例可复现


# ============================================================
# 0. 通用工具：元数据 / 答案卡 / 假界面截图
# ============================================================
def _write_meta(case_dir: Path, case_id: str, title: str, domain: str,
                link_dependency, symptom: str, window=None) -> None:
    meta = {
        "case_id": case_id,
        "title": title,
        "domain": domain,
        "link_dependency": list(link_dependency or []),
        "symptom": symptom,
    }
    if window:
        meta["time_window"] = [window[0].isoformat(), window[1].isoformat()]
    (case_dir / "case_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_truth(case_dir: Path, injected: str, expected_track: str,
                 root_cause: str, red_herrings: str, rag_hit: str) -> None:
    text = f"""# 真相卡（答案卡）—— 请先看 AI 的 RCA 报告，再回来对答案

- **人为注入的故障**：{injected}
- **真实根因**：{root_cause}
- **期望分诊轨道**：{expected_track}
- **干扰项（考验点）**：{red_herrings}
- **RAG 应命中**：{rag_hit}
"""
    (case_dir / "ground_truth.md").write_text(text, encoding="utf-8")


def _draw_screen(path, title: str, lines, status: str = "", status_color=(80, 180, 80)) -> None:
    """用 Pillow 画一张 1408x792 的假车机界面（英文标注，规避中文字体问题）。
    Pillow 未安装时跳过截图生成（不影响日志/步迹等诊断证据）。"""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print(f"  [跳过截图] 未安装 Pillow，跳过 {Path(path).name}（不影响诊断流程）")
        return
    img = Image.new("RGB", (1408, 792), (28, 28, 34))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 1408, 80], fill=(18, 18, 24))
    d.text((40, 28), title, fill=(255, 255, 255))
    y = 130
    for ln in lines:
        d.rectangle([60, y, 1348, y + 90], fill=(52, 52, 62))
        d.text((90, y + 32), ln, fill=(225, 225, 228))
        y += 115
    if status:
        d.rectangle([60, y, 600, y + 70], fill=status_color)
        d.text((90, y + 24), status, fill=(255, 255, 255))
    img.save(path)


def _write_steps(case_dir: Path, steps: list) -> None:
    with open(case_dir / "ui_steps.jsonl", "w", encoding="utf-8") as f:
        for s in steps:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")


def _logcat_line(t: datetime, pid: str, tid: str, level: str, tag: str, msg: str) -> str:
    return f"{t.strftime('%m-%d %H:%M:%S.%f')[:-3]}  {pid}  {tid} {level} {tag}: {msg}\n"


# ============================================================
# 1. case_1_ui_crash：纯 UI 崩溃（证据门必须拦住轨B）
# ============================================================
def generate_case_ui_crash(case_dir: Path) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    t0 = BASE
    with open(case_dir / "tbox_logcat.txt", "w", encoding="utf-8") as f:
        # 常规噪音（不含任何链路签名词，确保证据门安静）
        for i in range(50):
            t = t0 + timedelta(seconds=i * 1.2)
            f.write(_logcat_line(t, "1000", "1050", "I", "ActivityManager",
                                 "CPU usage normal, memory stable"))
        for i in range(11):
            t = t0 + timedelta(seconds=i * 5.5)
            f.write(_logcat_line(t, "850", "1120", "D", "WifiMonitor",
                                 "Scanning for networks..."))
        # 干扰项 1：设置 App 启动成功（红色鲱鱼 —— 成功链路不代表本次失败）
        t = t0 + timedelta(seconds=20)
        f.write(_logcat_line(t, "1000", "1050", "I", "ActivityManager",
                             "Start proc com.android.settings"))
        f.write(_logcat_line(t + timedelta(seconds=0.4), "1000", "1050", "I",
                             "ActivityManager", "Displayed com.android.settings"))
        # 案发现场：音乐 App 打开即闪退
        crash = t0 + timedelta(seconds=60)
        f.write(_logcat_line(crash, "1000", "1050", "I", "ActivityManager",
                             "Start proc com.ivi.music.player"))
        f.write(_logcat_line(crash + timedelta(seconds=0.3), "4321", "4321", "E",
                             "AndroidRuntime", "FATAL EXCEPTION: main"))
        f.write(_logcat_line(crash + timedelta(seconds=0.3), "4321", "4321", "E",
                             "AndroidRuntime", "Process: com.ivi.music.player, PID: 4321"))
        f.write(_logcat_line(crash + timedelta(seconds=0.3), "4321", "4321", "E",
                             "AndroidRuntime",
                             "java.lang.ArrayIndexOutOfBoundsException: index=5, length=4"))
        f.write(_logcat_line(crash + timedelta(seconds=0.3), "4321", "4321", "E",
                             "AndroidRuntime",
                             "    at com.ivi.music.player.audio.AudioFocusManager.requestFocus(AudioFocusManager.java:214)"))
        f.write(_logcat_line(crash + timedelta(seconds=0.3), "4321", "4321", "E",
                             "AndroidRuntime",
                             "    at com.ivi.music.player.MediaService.onStartCommand(MediaService.java:88)"))
        f.write(_logcat_line(crash + timedelta(seconds=0.8), "1000", "1050", "I",
                             "ActivityManager", "Process com.ivi.music.player (pid 4321) has died"))
        f.write(_logcat_line(crash + timedelta(seconds=1.2), "1000", "1050", "E",
                             "ActivityManager",
                             "ANR in com.ivi.music.player (com.ivi.music.player/.MainActivity)"))

    # 执行步迹（step_recorder 同款 schema）
    _write_steps(case_dir, [
        {"step": 1, "action": "TAP", "target_id": "12", "thought": "点击音乐 App 图标",
         "anomaly_handled": False, "screenshot": "step_01.png"},
        {"step": 2, "action": "TAP", "target_id": "3", "thought": "等待启动页",
         "anomaly_handled": False, "screenshot": "step_02.png"},
        {"step": 3, "action": "ASSERT", "target_id": "", "thought": "断言进入播放页",
         "anomaly_handled": False, "screenshot": "step_03.png"},
        {"step": 4, "action": "FAIL", "target_id": "", "thought": "App 闪退回桌面，断言失败",
         "anomaly_handled": False, "screenshot": "step_04.png"},
    ])

    _draw_screen(case_dir / "step_01.png", "IVI Home",
                 ["Music", "Settings", "Camera", "Contacts"])
    _draw_screen(case_dir / "step_04.png", "IVI Home (after crash)",
                 ["Music", "Settings", "Camera", "Contacts"],
                 status="Music app crashed, back to home", status_color=(180, 70, 70))

    _write_meta(case_dir, "case_1_ui_crash", "座舱音乐 App 打开即闪退",
                "ui", [], "点击音乐 App 后闪退回桌面，无法进入播放页",
                window=(crash - timedelta(seconds=5), crash + timedelta(seconds=10)))
    _write_truth(
        case_dir,
        injected="AudioFocusManager.requestFocus() 数组越界崩溃（index=5, length=4）",
        expected_track="仅轨A（UI/座舱域）。日志无任何链路签名，证据门应拦截轨B",
        root_cause="com.ivi.music.player 的 AudioFocusManager 未做空数组边界校验，"
                   "启动即抛 ArrayIndexOutOfBoundsException 导致进程死亡",
        red_herrings="1) 设置 App 成功启动的正常链路；2) 大量 ActivityManager/WifiMonitor 噪音",
        rag_hit="Jira-9001（座舱音乐 App 音频焦点崩溃）")
    # 机器可读答案卡（evaluate.py 评测用）
    write_truth(case_dir, build_truth(
        expected_track={"ui": True, "telematics": False},
        arbitration_direction="single",
        root_cause_keywords=["ArrayIndexOutOfBoundsException", "数组越界", "AudioFocusManager"],
        responsibility_keywords=["座舱", "音频", "音乐", "应用", "AudioFocus"],
        rag_hit="Jira-9001",
        note="AudioFocusManager.requestFocus 数组越界崩溃，纯 UI 域失败，轨B 不应启动"))


# ============================================================
# 2-4. 车联网链路三案例（复用混沌生成器）
# ============================================================
def generate_telematics_cases(demo_dir: Path) -> None:
    specs = [
        ("case_2_can_congestion", 1, BASE + timedelta(minutes=10),
         "远程解锁超时：CAN 总线被 ECM 心跳风暴抢占",
         "手机 App 远程解锁失败，云端报 TIMEOUT_NO_RESPONSE",
         "ECM 固件缺陷：高优心跳 0x0C1 以 10ms 异常刷帧，仲裁抢占全部带宽，"
         "低优先级解锁指令 0x3F2 堆积超时（历史 Jira-8832 旧病复发）",
         "60 秒窗口内的成功 lock 业务链路 + 0x1A4/0x2B5 心跳噪音",
         "Jira-8832"),
        ("case_3_network_offline", 2, BASE + timedelta(minutes=20),
         "车库弱网：T-BOX MQTT 客户端静默死锁",
         "地库中远程控车失败，云端报 TIMEOUT_OFFLINE",
         "弱网（RSSI -95dBm）下 T-BOX MQTT 客户端重连机制失效，socket 死锁，"
         "指令根本没走到 CAN 发送环节（历史 Jira-9022）",
         "成功 lock 链路 + 常规噪音；本案失败链路无任何 CAN 报文（考验不误判总线）",
         "Jira-9022"),
        ("case_4_hardware_fault", 3, BASE + timedelta(minutes=30),
         "执行器机械故障：BCM 反馈 0x3F3 携带 0xEE 错误码",
         "远程解锁报 HARDWARE_FAULT_0xEE",
         "车门执行电机卡滞，BCM 收到 0x3F2 后通过 0x3F3 返回错误码 0xEE"
         "（硬件故障而非通信故障，历史 Jira-9155）",
         "成功 lock 链路 + 噪音；本案 CAN 收发均正常（考验不被「通信超时」带偏）",
         "Jira-9155"),
    ]
    for name, sid, base_time, title, symptom, cause, red, rag in specs:
        case_dir = demo_dir / name
        generate_scenario(case_dir, sid, base_time)
        _write_meta(case_dir, name, title, "telematics",
                    ["cloud", "tbox", "can"], symptom)
        _write_truth(case_dir,
                     injected=f"链路故障（scenario {sid}）",
                     expected_track="仅轨B（车联网链路域）",
                     root_cause=cause,
                     red_herrings=red,
                     rag_hit=rag)


# ============================================================
# 5. case_5_ui_but_link_ok：链路健康但 UI 崩溃（反向仲裁专场）
# ============================================================
def generate_case_ui_but_link_ok(case_dir: Path) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    t0 = BASE + timedelta(minutes=40)
    req = "req_arb_8888"

    # ---- 链路证据：一切正常，完整闭环 ----
    with open(case_dir / "mqtt_cloud.log", "w", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": t0.isoformat(), "topic": "/app/req",
                            "payload": {"cmd": "unlock", "req_id": req}, "level": "INFO"}) + "\n")
        f.write(json.dumps({"timestamp": (t0 + timedelta(seconds=1.5)).isoformat(),
                            "topic": "/vehicle/tbox/status",
                            "payload": {"status": "SUCCESS", "req_id": req}, "level": "INFO"}) + "\n")

    with open(case_dir / "tbox_logcat.txt", "w", encoding="utf-8") as f:
        f.write(_logcat_line(t0 + timedelta(seconds=0.5), "1011", "2045", "I", "TBoxMqtt",
                             f"Received cmd: unlock, req: {req}"))
        f.write(_logcat_line(t0 + timedelta(seconds=0.6), "1011", "2046", "I", "CanHal",
                             "Enqueuing CAN ID: 0x3F2, Data: 01 (Unlock)"))
        f.write(_logcat_line(t0 + timedelta(seconds=1.0), "1011", "2046", "I", "VehicleControl",
                             "Door unlock executed OK. BCM status: 00"))
        # ★ 真正的故障：链路已成功，座舱 App 处理状态推送时崩溃
        crash = t0 + timedelta(seconds=1.6)
        f.write(_logcat_line(crash, "5566", "5566", "E", "AndroidRuntime",
                             "FATAL EXCEPTION: StatusSyncService"))
        f.write(_logcat_line(crash, "5566", "5566", "E", "AndroidRuntime",
                             "Process: com.ivi.remote, PID: 5566"))
        f.write(_logcat_line(crash, "5566", "5566", "E", "AndroidRuntime",
                             "java.lang.NullPointerException: statusPayload is null"))
        f.write(_logcat_line(crash, "5566", "5566", "E", "AndroidRuntime",
                             "    at com.ivi.remote.sync.StatusSyncService.onStatusPush(StatusSyncService.java:56)"))
        f.write(_logcat_line(crash + timedelta(seconds=0.6), "1000", "1050", "I",
                             "ActivityManager", "Process com.ivi.remote (pid 5566) has died"))

    with open(case_dir / "can_trace.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Timestamp", "ID", "Dir", "DLC", "Data"])
        w.writerow([f"{(t0 + timedelta(seconds=0.7)).timestamp():.6f}", "0x3F2", "TX", "8",
                    "01 00 00 00 00 00 00 00"])
        w.writerow([f"{(t0 + timedelta(seconds=0.9)).timestamp():.6f}", "0x3F3", "RX", "8",
                    "00 00 00 00 00 00 00 00"])

    # ---- UI 侧证据：界面显示失败 ----
    _write_steps(case_dir, [
        {"step": 1, "action": "TAP", "target_id": "7", "thought": "打开远程控制页",
         "anomaly_handled": False, "screenshot": "step_01.png"},
        {"step": 2, "action": "TAP", "target_id": "2", "thought": "点击车门解锁",
         "anomaly_handled": False, "screenshot": "step_02.png"},
        {"step": 3, "action": "ASSERT", "target_id": "", "thought": "断言显示解锁成功",
         "anomaly_handled": False, "screenshot": "step_03.png"},
        {"step": 4, "action": "FAIL", "target_id": "",
         "thought": "界面显示执行失败，断言不通过", "anomaly_handled": False,
         "screenshot": "step_04.png"},
    ])
    _draw_screen(case_dir / "step_01.png", "Remote Control",
                 ["Door Unlock", "Window Open", "A/C On"])
    _draw_screen(case_dir / "step_04.png", "Remote Control",
                 ["Door Unlock", "Window Open", "A/C On"],
                 status="Door Unlock: FAILED", status_color=(180, 70, 70))

    _write_meta(case_dir, "case_5_ui_but_link_ok", "远程解锁显示失败但链路实际成功",
                "mixed", ["cloud", "tbox", "can"],
                "车机显示远程解锁失败，但云端状态为 SUCCESS",
                window=(t0 - timedelta(seconds=5), crash + timedelta(seconds=5)))
    _write_truth(
        case_dir,
        injected="座舱 App StatusSyncService.onStatusPush() 空指针崩溃",
        expected_track="双轨并行 + 反向仲裁：轨B 应证实链路闭环正常（0x3F3 返回 00、"
                       "云端 SUCCESS），责任回摆座舱应用层，由轨A 收口",
        root_cause="链路层无责：CAN 0x3F3 已返回成功码 00、云端已推送 SUCCESS；"
                   "真正根因是 com.ivi.remote 状态同步服务处理推送时 NPE 崩溃，"
                   "导致 UI 未刷新并显示失败",
        red_herrings="界面上的 FAILED 文案（诱导误判为链路超时）；链路证据中的正常报文流",
        rag_hit="Jira-9002（座舱状态同步服务崩溃）")
    # 机器可读答案卡（evaluate.py 评测用）
    write_truth(case_dir, build_truth(
        expected_track={"ui": True, "telematics": True},
        arbitration_direction="telematics->ui",
        root_cause_keywords=["NullPointerException", "空指针", "状态同步", "StatusSync"],
        responsibility_keywords=["座舱", "应用", "状态同步", "StatusSync", "软件"],
        rag_hit="Jira-9002",
        note="链路无责（CAN 0x3F3 返回 00、云端 SUCCESS），座舱 App 状态推送 NPE 崩溃",
    ))


# ============================================================
# 5.5 case_6_field_noisy：场域仿真鲁棒性案例（坏行/重复/未知 CAN ID）
# ============================================================
def generate_case_field_noisy(case_dir: Path) -> None:
    """场域仿真：模拟真实采集日志的"脏数据"特征，考验解析与对齐鲁棒性。

    植入的脏数据（都有意保留，评估管线不崩溃 + 正确降噪）：
    1. MQTT 重复下发（同 req_id 两条）+ 1 条截断坏 JSON + 最终 SUCCESS 干扰；
    2. Logcat：无时间戳截断行、带年份前缀行（真实变体）、重复行；
    3. CAN：未知诊断报文 0x7E8 以 5ms 高频洪泛抢占总线（字典未定义 -> 诚实返回未知）。
    """
    case_dir.mkdir(parents=True, exist_ok=True)
    t0 = BASE + timedelta(minutes=50)
    req = "req_dup_9001"

    with open(case_dir / "mqtt_cloud.log", "w", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": t0.isoformat(), "topic": "/app/req",
                            "payload": {"cmd": "unlock", "req_id": req}, "level": "INFO"}) + "\n")
        f.write(json.dumps({"timestamp": (t0 + timedelta(seconds=0.2)).isoformat(),
                            "topic": "/app/req",
                            "payload": {"cmd": "unlock", "req_id": req}, "level": "INFO"}) + "\n")
        f.write("{'broken truncated json\n")          # 坏行：截断 JSON
        f.write(json.dumps({"timestamp": (t0 + timedelta(seconds=6.0)).isoformat(),
                            "topic": "/vehicle/tbox/status",
                            "error": "TIMEOUT_NO_RESPONSE", "req_id": req, "level": "ERROR"}) + "\n")
        f.write(json.dumps({"timestamp": (t0 + timedelta(seconds=8.0)).isoformat(),
                            "topic": "/vehicle/tbox/status",
                            "payload": {"status": "SUCCESS", "req_id": req}, "level": "INFO"}) + "\n")

    with open(case_dir / "tbox_logcat.txt", "w", encoding="utf-8") as f:
        f.write(_logcat_line(t0 + timedelta(seconds=0.3), "1011", "2045", "I", "TBoxMqtt",
                             f"Received cmd: unlock, req: {req}"))
        f.write(_logcat_line(t0 + timedelta(seconds=0.4), "1011", "2046", "I", "CanHal",
                             "Enqueuing CAN ID: 0x3F2, Data: 01 (Unlock)"))
        f.write(_logcat_line(t0 + timedelta(seconds=0.4), "1011", "2046", "I", "CanHal",
                             "Enqueuing CAN ID: 0x3F2, Data: 01 (Unlock)"))   # 重复行
        f.write("这一行被截断了，没有时间戳\n")                                 # 坏行
        f.write(f"2026-10-04 {t0.strftime('%H:%M:%S.%f')[:-3]}  1011  2046 I CanHal: "
                "year-prefixed variant line\n")                                  # 带年份变体
        f.write(_logcat_line(t0 + timedelta(seconds=5.0), "1011", "2046", "E", "CanHal",
                             "CAN TX Timeout! Buffer full. ID: 0x3F2"))

    with open(case_dir / "can_trace.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Timestamp", "ID", "Dir", "DLC", "Data"])
        w.writerow([f"{(t0 + timedelta(seconds=0.5)).timestamp():.6f}", "0x3F2", "TX", "8",
                    "01 00 00 00 00 00 00 00"])
        # 未知诊断报文 0x7E8 高频洪泛（80 帧 @5ms），抢占总线
        for i in range(80):
            t = t0 + timedelta(seconds=0.6) + timedelta(milliseconds=i * 5)
            w.writerow([f"{t.timestamp():.6f}", "0x7E8", "RX", "8", "02 01 0D 00 00 00 00 00"])
        # 常规噪音若干
        for i in range(20):
            t = t0 + timedelta(seconds=i * 0.1)
            w.writerow([f"{t.timestamp():.6f}", "0x1A4", "RX", "8", "00 12 34 00 00 00 00 00"])

    _write_meta(case_dir, "case_6_field_noisy", "场域仿真：脏数据 + 未知诊断报文洪泛",
                "telematics", ["cloud", "tbox", "can"],
                "远程解锁超时；日志含坏行/重复行/未知 CAN 报文",
                window=(t0 - timedelta(seconds=5), t0 + timedelta(seconds=10)))
    _write_truth(
        case_dir,
        injected="未知诊断报文 0x7E8 高频洪泛 + 多种脏数据（截断行/带年份行/重复行/重复下发）",
        expected_track="仅轨B；脏数据不应导致崩溃，坏行应被计数并跳过",
        root_cause="诊断模块异常：0x7E8 诊断报文以 5ms 高频刷帧抢占总线，0x3F2 解锁指令"
                   "无法发出导致超时（重复下发加重了总线负载）",
        red_herrings="最终 SUCCESS 状态（诱导误判链路已恢复）；0x7E8 不在内置字典（考验未知 ID 诚实降级）",
        rag_hit="无（未知诊断报文，期望 RAG 诚实返回未检索到）")
    write_truth(case_dir, build_truth(
        expected_track={"ui": False, "telematics": True},
        arbitration_direction="single",
        root_cause_keywords=["0x7E8", "诊断", "洪泛", "抢占"],
        responsibility_keywords=["诊断", "OBD", "网关", "ECU", "车联", "协议", "TBOX"],
        rag_hit="",
        note="未知诊断报文 0x7E8 洪泛抢占总线；管线须在不崩溃的前提下跳过坏行并计数",
    ))


# ============================================================
# 6. 总入口
# ============================================================
def inject_all_demo_cases(demo_dir=None) -> dict:
    """生成全部演示案例。demo_dir 为演示集根目录（默认 data_output/demo_cases）。"""
    from config.settings import settings
    if demo_dir is None:
        demo_dir = settings.data_dir / "demo_cases"
    demo_dir = Path(demo_dir)
    generate_case_ui_crash(demo_dir / "case_1_ui_crash")
    generate_telematics_cases(demo_dir)
    generate_case_ui_but_link_ok(demo_dir / "case_5_ui_but_link_ok")
    generate_case_field_noisy(demo_dir / "case_6_field_noisy")
    return {"demo_dir": str(demo_dir),
            "cases": sorted(p.name for p in demo_dir.iterdir() if p.is_dir())}


def main():
    from config.settings import settings
    result = inject_all_demo_cases(settings.data_dir / "demo_cases")
    print("=" * 70)
    print("故障注入演示集已生成:")
    for c in result["cases"]:
        print(f"  - {c}")
    print()
    print("下一步:")
    print("  python -m rca_engine.cli cases")
    print("  python -m rca_engine.cli diagnose --case data_output/demo_cases/<案例>")
    print("  （先看 AI 报告，再看案例目录里的 ground_truth.md 对答案）")
    print("=" * 70)


if __name__ == "__main__":
    main()
