"""车联网三域混沌日志生成器（经典 3 案例，供轨B 演示与回归基准）。

保留原工程语义：60 秒窗口内铺设真实背景噪音 + 一次成功业务干扰 + 一次案发现场。
用法：
  python -m mock_data.generate_logs            # 生成 data_output/case_1..3
"""
import csv
import json
import os
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mock_data.truth_schema import build_truth, write_truth  # noqa: E402

# 每个注入场景的元数据与真相卡（evaluate.py 评测基准的依据）
SCENARIO_INFO = {
    1: {
        "title": "远程解锁超时：CAN 总线被 ECM 心跳风暴抢占",
        "symptom": "手机 App 远程解锁失败，云端报 TIMEOUT_NO_RESPONSE",
        "truth": build_truth(
            expected_track={"ui": False, "telematics": True},
            arbitration_direction="single",
            root_cause_keywords=["0x0C1", "心跳", "抢占", "风暴", "ECM"],
            responsibility_keywords=["ECM", "动力域"],
            rag_hit="Jira-8832",
            note="ECM 高优心跳 10ms 异常刷帧，CAN 仲裁抢占全部带宽，0x3F2 堆积超时",
        ),
    },
    2: {
        "title": "车库弱网：T-BOX MQTT 客户端静默死锁",
        "symptom": "地库中远程控车失败，云端报 TIMEOUT_OFFLINE",
        "truth": build_truth(
            expected_track={"ui": False, "telematics": True},
            arbitration_direction="single",
            root_cause_keywords=["弱网", "MQTT", "死锁", "socket", "重连"],
            responsibility_keywords=["T-BOX", "TBOX", "网络", "MQTT", "通信"],
            rag_hit="Jira-9022",
            note="弱网下 MQTT 客户端重连失效、socket 死锁，指令未走到 CAN 发送环节",
        ),
    },
    3: {
        "title": "执行器机械故障：BCM 反馈 0x3F3 携带 0xEE 错误码",
        "symptom": "远程解锁报 HARDWARE_FAULT_0xEE",
        "truth": build_truth(
            expected_track={"ui": False, "telematics": True},
            arbitration_direction="single",
            root_cause_keywords=["0xEE", "电机", "硬件", "执行器", "BCM"],
            responsibility_keywords=["硬件", "执行器", "BCM"],
            rag_hit="Jira-9155",
            note="车门执行电机卡滞，BCM 经 0x3F3 返回 0xEE 硬件故障码（CAN 收发均正常）",
        ),
    },
}


def generate_scenario(output_dir, scenario_id, current_time):
    """生成一个 60 秒的混沌案例（噪音 + 成功干扰 + 注入故障）。"""
    os.makedirs(output_dir, exist_ok=True)
    mqtt_file = os.path.join(output_dir, 'mqtt_cloud.log')
    logcat_file = os.path.join(output_dir, 'tbox_logcat.txt')
    can_file = os.path.join(output_dir, 'can_trace.csv')

    start_time = current_time - timedelta(seconds=60)

    with open(mqtt_file, 'w', encoding='utf-8') as f_mqtt, \
         open(logcat_file, 'w', encoding='utf-8') as f_logcat, \
         open(can_file, 'w', newline='', encoding='utf-8') as f_can:

        can_writer = csv.writer(f_can)
        can_writer.writerow(["Timestamp", "ID", "Dir", "DLC", "Data"])

        # ---------- 1. 60 秒全局背景噪音 ----------
        for ms in range(0, 60000, 20):
            now = start_time + timedelta(milliseconds=ms)
            if ms % 50 == 0:
                can_writer.writerow([f"{now.timestamp():.6f}", "0x1A4", "RX", "8", "00 12 34 00 00 00 00 00"])
            if ms % 100 == 0:
                can_writer.writerow([f"{now.timestamp():.6f}", "0x2B5", "RX", "8", "AA BB CC DD EE FF 00 11"])
            if ms % 1200 == 0:
                f_logcat.write(f"{now.strftime('%m-%d %H:%M:%S.%f')[:-3]}  1000  1050 I ActivityManager: CPU usage normal, memory stable\n")
            if ms % 5500 == 0:
                f_logcat.write(f"{now.strftime('%m-%d %H:%M:%S.%f')[:-3]}   850  1120 D WifiMonitor: Scanning for networks...\n")
            if ms % 8000 == 0:
                f_logcat.write(f"{now.strftime('%m-%d %H:%M:%S.%f')[:-3]}  1011  2045 I TBoxMqtt: MQTT KeepAlive Ping Sent\n")
            if ms % 15000 == 0:
                f_mqtt.write(json.dumps({"timestamp": now.isoformat(), "topic": "/vehicle/status",
                                         "payload": {"batt": 85, "speed": 0}, "level": "DEBUG"}) + "\n")

        # ---------- 2. 一次完全成功的业务干扰（考验 AI 不被误导） ----------
        success_req = f"req_succ_{random.randint(1000, 9999)}"
        s_time = start_time + timedelta(seconds=random.randint(10, 20))
        f_mqtt.write(json.dumps({"timestamp": s_time.isoformat(), "topic": "/app/req",
                                 "payload": {"cmd": "lock", "req_id": success_req}, "level": "INFO"}) + "\n")
        f_logcat.write(f"{(s_time + timedelta(seconds=0.5)).strftime('%m-%d %H:%M:%S.%f')[:-3]}  1011  2045 I TBoxMqtt: Received cmd: lock, req: {success_req}\n")
        f_logcat.write(f"{(s_time + timedelta(seconds=0.6)).strftime('%m-%d %H:%M:%S.%f')[:-3]}  1011  2046 I CanHal: Enqueuing CAN ID: 0x3F2, Data: 00 (Lock)\n")
        can_writer.writerow([f"{(s_time + timedelta(seconds=0.7)).timestamp():.6f}", "0x3F2", "TX", "8", "00 00 00 00 00 00 00 00"])
        f_logcat.write(f"{(s_time + timedelta(seconds=0.8)).strftime('%m-%d %H:%M:%S.%f')[:-3]}  1011  2046 I CanHal: TX Success ID: 0x3F2\n")
        f_mqtt.write(json.dumps({"timestamp": (s_time + timedelta(seconds=1.5)).isoformat(), "topic": "/vehicle/tbox/status",
                                 "payload": {"status": "SUCCESS", "req_id": success_req}, "level": "INFO"}) + "\n")

        # ---------- 3. 案发现场（40~50 秒处注入） ----------
        bug_req = f"req_fail_{random.randint(1000, 9999)}"
        bug_time = start_time + timedelta(seconds=random.randint(40, 50))

        if scenario_id == 1:
            # 场景 1：ECM 心跳风暴（0x0C1 @10ms）抢占 CAN 总线 -> 0x3F2 发送超时
            f_mqtt.write(json.dumps({"timestamp": bug_time.isoformat(), "topic": "/app/req",
                                     "payload": {"cmd": "unlock", "req_id": bug_req}, "level": "INFO"}) + "\n")
            f_logcat.write(f"{(bug_time + timedelta(seconds=0.5)).strftime('%m-%d %H:%M:%S.%f')[:-3]}  1011  2045 I TBoxMqtt: Received cmd: unlock, req: {bug_req}\n")
            f_logcat.write(f"{(bug_time + timedelta(seconds=0.6)).strftime('%m-%d %H:%M:%S.%f')[:-3]}  1011  2046 I CanHal: Enqueuing CAN ID: 0x3F2, Data: 01 (Unlock)\n")
            for i in range(600):
                t = bug_time + timedelta(seconds=0.7) + timedelta(milliseconds=i * 10)
                can_writer.writerow([f"{t.timestamp():.6f}", "0x0C1", "RX", "8", "FF FF FF FF FF FF FF FF"])
            f_logcat.write(f"{(bug_time + timedelta(seconds=5.6)).strftime('%m-%d %H:%M:%S.%f')[:-3]}  1011  2046 E CanHal: CAN TX Timeout! Buffer full. ID: 0x3F2\n")
            f_mqtt.write(json.dumps({"timestamp": (bug_time + timedelta(seconds=6.0)).isoformat(), "topic": "/vehicle/tbox/status",
                                     "error": "TIMEOUT_NO_RESPONSE", "req_id": bug_req, "level": "ERROR"}) + "\n")

        elif scenario_id == 2:
            # 场景 2：车库弱网，MQTT 客户端静默死锁（根本走不到发 CAN）
            f_mqtt.write(json.dumps({"timestamp": bug_time.isoformat(), "topic": "/app/req",
                                     "payload": {"cmd": "unlock", "req_id": bug_req}, "level": "INFO"}) + "\n")
            f_logcat.write(f"{(bug_time - timedelta(seconds=5)).strftime('%m-%d %H:%M:%S.%f')[:-3]}  1011  2045 W TBoxMqtt: Signal quality poor. RSSI: -95dBm\n")
            f_logcat.write(f"{(bug_time + timedelta(seconds=1)).strftime('%m-%d %H:%M:%S.%f')[:-3]}  1011  2045 E TBoxMqtt: Ping Timeout. Client Socket Deadlock.\n")
            f_logcat.write(f"{(bug_time + timedelta(seconds=3)).strftime('%m-%d %H:%M:%S.%f')[:-3]}  1011  2045 I TBoxMqtt: Attempting to restart network manager...\n")
            f_mqtt.write(json.dumps({"timestamp": (bug_time + timedelta(seconds=7.0)).isoformat(), "topic": "/vehicle/tbox/status",
                                     "error": "TIMEOUT_OFFLINE", "req_id": bug_req, "level": "ERROR"}) + "\n")

        elif scenario_id == 3:
            # 场景 3：执行器损坏，BCM 反馈 0x3F3 携带 0xEE 硬件错误码
            f_mqtt.write(json.dumps({"timestamp": bug_time.isoformat(), "topic": "/app/req",
                                     "payload": {"cmd": "unlock", "req_id": bug_req}, "level": "INFO"}) + "\n")
            f_logcat.write(f"{(bug_time + timedelta(seconds=0.5)).strftime('%m-%d %H:%M:%S.%f')[:-3]}  1011  2045 I TBoxMqtt: Received cmd: unlock, req: {bug_req}\n")
            f_logcat.write(f"{(bug_time + timedelta(seconds=0.6)).strftime('%m-%d %H:%M:%S.%f')[:-3]}  1011  2046 I CanHal: Enqueuing CAN ID: 0x3F2, Data: 01 (Unlock)\n")
            can_time_tx = bug_time + timedelta(seconds=0.7)
            f_logcat.write(f"{can_time_tx.strftime('%m-%d %H:%M:%S.%f')[:-3]}  1011  2046 I CanHal: TX Success ID: 0x3F2\n")
            can_writer.writerow([f"{can_time_tx.timestamp():.6f}", "0x3F2", "TX", "8", "01 00 00 00 00 00 00 00"])
            can_time_rx = bug_time + timedelta(seconds=0.9)
            can_writer.writerow([f"{can_time_rx.timestamp():.6f}", "0x3F3", "RX", "8", "EE 00 00 00 00 00 00 00"])
            f_logcat.write(f"{can_time_rx.strftime('%m-%d %H:%M:%S.%f')[:-3]}  1011  2046 E VehicleControl: BCM reported hardware fault! Code: 0xEE\n")
            f_mqtt.write(json.dumps({"timestamp": (bug_time + timedelta(seconds=1.5)).isoformat(), "topic": "/vehicle/tbox/status",
                                     "error": "HARDWARE_FAULT_0xEE", "req_id": bug_req, "level": "ERROR"}) + "\n")

    # ---------- 4. 元数据与答案卡（evaluate.py 评测基准的依据） ----------
    info = SCENARIO_INFO.get(scenario_id)
    if info:
        meta = {
            "case_id": Path(output_dir).name,
            "title": info["title"],
            "domain": "telematics",
            "link_dependency": ["cloud", "tbox", "can"],
            "symptom": info["symptom"],
        }
        with open(os.path.join(output_dir, "case_meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        write_truth(output_dir, info["truth"])


def main():
    from config.settings import settings
    scenarios = [
        ("case_1_can_congestion", 1),
        ("case_2_network_offline", 2),
        ("case_3_hardware_fault", 3),
    ]
    for folder, s_id in scenarios:
        out = settings.data_dir / folder
        generate_scenario(out, s_id, datetime.now())
        print(f"已生成混沌测试集: {folder} (正常业务 + 海量噪音 + 注入故障)")


if __name__ == "__main__":
    main()
