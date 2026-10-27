"""批量 UI 实测 runner（针对 Android Studio 模拟器 / ADB 真机）。

用途（P1 真机实测）：
- 每个自然语言任务重复 N 次，统计成功率与耗时；
- 每次运行落盘完整证据包（步迹 + 截图 + 失败时 logcat 转储）；
- 可选 --rca-on-failure：失败案例自动接入 incident_bridge -> 双轨 RCA，
  实现"UI 实测失败 -> 自动定责"的真实闭环（自举验证）。

用法：
  python -m ui_agent.batch_runner --repeats 5 --rca-on-failure
  python -m ui_agent.batch_runner --repeats 3 --tasks ui_settings_open,ui_camera_open
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 默认任务套件（全部针对原生 Android 系统应用，模拟器/车机均可用）
# 设计原则：goal 描述操作路径，expected_state 提供独立验收标准——
# AI 报 DONE 后引擎会另做一次界面断言，双重把关，杜绝"浅尝即止"。
DEFAULT_SUITE = [
    {"case_id": "ui_settings_open",
     "goal": "打开系统的 Settings(设置) 应用，看到设置列表页（含 Network & internet、Display 等选项）即完成任务 (DONE)。",
     "expected_state": "当前界面是系统设置列表页，包含 Network & internet、Display 等设置项",
     "domain": "ui", "link_dependency": []},
    {"case_id": "ui_settings_display_deep",
     "goal": "打开 Settings(设置) 应用后，点击 Display 设置项进入显示设置页，"
             "看到亮度(Brightness level)或自适应亮度(Adaptive brightness)相关选项后任务完成 (DONE)。",
     "expected_state": "当前界面是设置应用中的 Display(显示) 详情页，含亮度或自适应亮度设置项",
     "domain": "ui", "link_dependency": []},
    {"case_id": "ui_contacts_open",
     "goal": "打开电话(Phone)应用（本系统通讯录 Contacts 集成在电话应用内），"
             "进入拨号盘、联系人列表或蓝牙配对提示页任一界面即完成任务 (DONE)。",
     "expected_state": "当前界面是电话应用界面（拨号盘、联系人列表或蓝牙配对提示页之一）",
     "domain": "ui", "link_dependency": []},
    {"case_id": "ui_connected_devices_page",
     "goal": "打开 Settings(设置) 应用，点击 Connected devices 设置项，"
             "看到已连接设备或连接偏好信息后任务完成 (DONE)。",
     "expected_state": "当前界面是设置应用的 Connected devices 页面，显示已连接设备或连接偏好信息",
     "domain": "ui", "link_dependency": []},
    {"case_id": "ui_sound_page",
     "goal": "打开 Settings(设置) 应用，点击 Sound 设置项，"
             "看到媒体音量或声音相关设置项后任务完成 (DONE)。",
     "expected_state": "当前界面是设置应用的 Sound 页面，显示音量或声音相关设置项",
     "domain": "ui", "link_dependency": []},
]

# 深链路套件：多级导航 + 状态变更 + 多应用切换（在基线之上测真实 E2E 能力）
# 目标环境：Android Automotive with Google APIs (x86_64) 模拟器。
# 设计原则：只用在该环境"已确认存在"的 UI 元素（来自多轮实测观察）——
#   Settings 列表：Network & internet / Connected devices / Notifications / Sound / Display；
#   Display 页：Brightness level 滑块、Adaptive brightness 开关（无 Dark theme 开关）；
#   桌面底部常驻栏：Phone/Messages/Maps/Settings；Phone 打开后为蓝牙配对提示页。
#   注意：该镜像左下角"应用抽屉"图标点击无响应、相机无可用入口——相关任务已移除。
DEEP_SUITE = [
    {"case_id": "ui_settings_brightness_max",
     "goal": "打开 Settings(设置) 应用，进入 Display(显示) 设置页，把 Brightness level(亮度) "
             "滑块拖到最右端最大值（对该滑块使用 SWIPE_RIGHT 框内向右拖动），"
             "确认滑块已到最大值后任务完成 (DONE)。",
     "expected_state": "当前界面是设置应用的 Display 页，Brightness level 滑块位于最右端（最大值）",
     "domain": "ui", "link_dependency": []},
    {"case_id": "ui_settings_network_page",
     "goal": "打开 Settings(设置) 应用，进入 Network & internet 页面，"
             "看到 Wi-Fi 或网络连接状态信息后任务完成 (DONE)。",
     "expected_state": "当前界面是设置应用的 Network & internet 页面，显示 Wi-Fi 或网络连接状态信息",
     "domain": "ui", "link_dependency": []},
    {"case_id": "ui_settings_roundtrip_nav",
     "goal": "打开 Settings(设置) 应用，进入 Display 页面，然后用 BACK 返回设置列表，"
             "再点击 Sound 进入声音设置页，看到音量相关设置项后任务完成 (DONE)。",
     "expected_state": "当前界面是设置应用的 Sound 页面，显示音量或声音相关设置项",
     "domain": "ui", "link_dependency": []},
    {"case_id": "ui_multi_app_switch",
     "goal": "依次完成：打开 Settings(设置) 应用 -> HOME 返回主桌面 -> 打开电话(Phone)应用，"
             "最后停留在电话应用界面后任务完成 (DONE)。",
     "expected_state": "当前界面是电话应用界面（拨号盘、联系人列表或蓝牙配对提示页之一）",
     "domain": "ui", "link_dependency": []},
]


# 基础设施类异常特征（网关超时/限流等，不属于被测系统缺陷）
INFRA_ERROR_KEYWORDS = ("VLM 请求失败", "Request timed out", "50507", "rate limit",
                        "Connection error", "Timed out", "服务繁忙")


def _is_infra_error(note: str) -> bool:
    return any(k in note for k in INFRA_ERROR_KEYWORDS)


def run_batch(suite=None, repeats: int = 3, rca_on_failure: bool = False,
              offline_rca: bool = True, settings=None, out_root=None,
              max_steps: int = 8) -> dict:
    from ui_agent.adb_controller import AdbController
    from ui_agent.vlm_client import VLMClient
    from ui_agent.som_marker import SoMMarker
    from ui_agent.step_recorder import StepRecorder
    from ui_agent.agent_engine import AgentEngine

    suite = suite or DEFAULT_SUITE
    out_root = Path(out_root) if out_root else Path(settings.data_dir) / "ui_runs"
    rows = []
    total_runs = len(suite) * repeats
    done = 0
    batch_t0 = time.time()

    for task in suite:
        for run_no in range(1, repeats + 1):
            done += 1
            print(f"\n[{task['case_id']}] 第 {run_no}/{repeats} 轮 "
                  f"(总进度 {done}/{total_runs}) ...")
            adb = AdbController()
            adb.wake_and_unlock()
            adb.press_home()
            time.sleep(1)
            recorder = StepRecorder(out_root, task["case_id"])
            engine = AgentEngine(
                adb_controller=adb,
                vlm_client=VLMClient(settings.api_key, base_url=settings.base_url,
                                     model_name=settings.vlm_model,
                                     timeout=settings.vlm_timeout,
                                     retries=settings.vlm_retries),
                som_marker=SoMMarker(),
                recorder=recorder,
                settings=settings,
                max_steps=max_steps)
            run_start = time.time()
            # 任务级执行：基础设施异常（网关超时/限流）冷却后整任务重试，不计入被测系统失败
            infra_retries = 0
            while True:
                try:
                    result = engine.execute_task(task["goal"], case_id=task["case_id"],
                                                 domain=task.get("domain", "ui"),
                                                 link_dependency=task.get("link_dependency"),
                                                 expected_state=task.get("expected_state"))
                    break
                except Exception as e:  # noqa: BLE001
                    note = str(e)
                    if _is_infra_error(note) and infra_retries < 2:
                        infra_retries += 1
                        cooldown = 60 * infra_retries
                        print(f"    [基础设施异常] {note[:80]}... {cooldown}s 冷却后重试任务"
                              f"（{infra_retries}/2）")
                        time.sleep(cooldown)
                        continue
                    result = {"success": False, "note": f"执行异常: {note}",
                              "run_dir": None, "infra_error": _is_infra_error(note)}
                    break
            duration = round(time.time() - run_start, 1)
            # 失败证据：把设备 logcat 转储进证据包（轨A 的 UI 证据直接可用）
            if not result["success"] and recorder.run_dir is not None:
                adb.dump_logcat(os.path.join(recorder.run_dir, "tbox_logcat.txt"))
            row = {"case": task["case_id"], "run": run_no, "success": bool(result["success"]),
                   "note": str(result.get("note", ""))[:200], "duration_s": duration,
                   "run_dir": result.get("run_dir"),
                   "infra_error": bool(result.get("infra_error"))}
            rows.append(row)
            elapsed = (time.time() - batch_t0) / 60
            avg_per_run = elapsed / done
            eta = avg_per_run * (total_runs - done)
            verdict = "PASS" if row["success"] else ("⚠基础设施" if row["infra_error"] else "FAIL")
            print(f"    -> {verdict} ({duration}s) {row['note'][:80]}")
            print(f"    已用时 {elapsed:.0f} 分钟，预计剩余约 {eta:.0f} 分钟")

    per_task = {}
    for row in rows:
        s = per_task.setdefault(row["case"], {"total": 0, "passed": 0, "infra": 0})
        s["total"] += 1
        s["passed"] += int(row["success"])
        s["infra"] += int(row["infra_error"])
    valid_runs = [r for r in rows if not r["infra_error"]]
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_runs": len(rows),
        "infra_error_runs": sum(int(r["infra_error"]) for r in rows),
        # 成功率只统计"有效轮次"（基础设施异常轮次单列，不污染被测系统指标）
        "overall_success_rate": round(sum(int(r["success"]) for r in valid_runs) / len(valid_runs), 4)
                                if valid_runs else 0.0,
        "per_task": {k: {"passed": v["passed"], "total": v["total"], "infra": v["infra"],
                         "valid": v["total"] - v["infra"],
                         "success_rate": round(v["passed"] / (v["total"] - v["infra"]), 4)
                                         if (v["total"] - v["infra"]) > 0 else 0.0}
                     for k, v in per_task.items()},
        "rows": rows,
        "rca": [],
    }

    # ---- 闭环：失败证据自动喂回 RCA ----
    if rca_on_failure:
        failures = [r for r in rows if not r["success"] and r.get("run_dir")]
        if failures:
            from incident_bridge.evidence_collector import EvidenceCollector
            from incident_bridge.triage import Triage
            from rca_engine.graph_pipeline import run_rca_pipeline
            from rca_engine.report_writer import ReportWriter
            triage = Triage(settings.evidence_gate_patterns)
            writer = ReportWriter(settings.reports_dir)
            offline = offline_rca or not settings.api_key
            llm, rag = None, None
            if not offline:
                from rca_engine.graph_pipeline import build_default_embeddings, build_default_llm
                from rca_engine.rag_store import RAGStore
                try:
                    llm = build_default_llm(settings)
                    rag = RAGStore(build_default_embeddings(settings),
                                   settings.rag_persist_dir, settings.knowledge_file)
                except Exception as e:  # noqa: BLE001
                    print(f"⚠ RCA 初始化失败，降级离线: {e}")
                    offline = True
            for f in failures:
                incident = EvidenceCollector.collect_from_run(f["run_dir"])
                if incident is None:
                    continue
                plan = triage.decide(incident)
                res = run_rca_pipeline(incident, plan, llm=llm, rag=rag, writer=writer,
                                       use_graph=True, offline=offline)
                summary["rca"].append({
                    "case": f["case"], "run": f["run"],
                    "arbitration": res.get("arbitration", {}),
                    "verdict": res.get("final", {}).get("final", {}),
                    "report_md": res.get("report_md_path", ""),
                })
                print(f"    [RCA] {f['case']}#{f['run']} -> "
                      f"{res.get('final', {}).get('final', {}).get('responsibility', '-')}")

    reports_dir = Path(settings.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    (reports_dir / f"ui_batch_{ts}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = reports_dir / f"ui_batch_{ts}.md"
    md_path.write_text(_to_markdown(summary), encoding="utf-8")
    summary["report_md"] = str(md_path)
    return summary


def _to_markdown(s: dict) -> str:
    infra = s.get("infra_error_runs", 0)
    lines = [
        "# UI 批量实测报告",
        f"- 生成时间：{s['generated_at']}",
        f"- 总运行 {s['total_runs']} 次，其中基础设施异常（网关超时/限流，不计入指标）{infra} 次",
        f"- 总体成功率（有效轮次口径）{s['overall_success_rate']:.1%}",
        "",
        "## 分任务统计",
        "| 任务 | 通过/有效 | 基础设施异常 | 成功率 |",
        "|---|---|---|---|",
    ]
    for k, v in s["per_task"].items():
        lines.append(f"| {k} | {v['passed']}/{v['valid']} | {v['infra']} | {v['success_rate']:.1%} |")
    lines += ["", "## 逐次明细",
              "| 任务 | 轮 | 结果 | 耗时(s) | 备注 |", "|---|---|---|---|---|"]
    for r in s["rows"]:
        if r["success"]:
            mark = "✅"
        elif r.get("infra_error"):
            mark = "⚠️基建"
        else:
            mark = "❌"
        lines.append(f"| {r['case']} | {r['run']} | {mark} | "
                     f"{r['duration_s']} | {r['note'][:60]} |")
    if s["rca"]:
        lines += ["", "## 失败案例 RCA 结论（测→析闭环）",
                  "| 案例 | 责任方 | 根因 | 报告 |", "|---|---|---|---|"]
        for r in s["rca"]:
            v = r.get("verdict") or {}
            lines.append(f"| {r['case']}#{r['run']} | {v.get('responsibility', '-')} | "
                         f"{str(v.get('root_cause', '-'))[:50]} | {Path(r.get('report_md', '')).name} |")
    lines.append("")
    lines.append("> 证据包位于 data_output/ui_runs/<case>/<ts>/，RCA 报告位于 reports/。")
    return "\n".join(lines)


def main() -> int:
    import logging
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(levelname)s - [%(module)s] - %(message)s",
                        datefmt="%H:%M:%S")
    parser = argparse.ArgumentParser(prog="batch_runner", description="模拟器批量 UI 实测")
    parser.add_argument("--repeats", type=int, default=3, help="每个任务重复次数（建议 3~10）")
    parser.add_argument("--max-steps", type=int, default=8,
                        help="单任务最大步数（默认 8；当前套件任务理想路径均 ≤6 步，收紧可止损）")
    parser.add_argument("--suite", choices=["default", "deep"], default="default",
                        help="任务套件：default=基线（打开页面级），deep=深链路（多级导航/状态变更）")
    parser.add_argument("--rca-on-failure", action="store_true", help="失败案例自动喂回 RCA")
    parser.add_argument("--offline-rca", action="store_true", help="RCA 走离线确定性模式")
    parser.add_argument("--tasks", default="", help="只跑指定任务（逗号分隔 case_id）")
    args = parser.parse_args()

    from config.settings import settings
    if not settings.api_key:
        print("错误：UI 实测需要 VLM。请先在 .env 配置 SILICONFLOW_API_KEY。")
        return 2

    # 设备预检
    from ui_agent.adb_controller import AdbController
    try:
        state = AdbController().run_adb_cmd(["get-state"])
        print(f"ADB 设备状态: {state}")
    except Exception as e:  # noqa: BLE001
        print(f"错误：未检测到 ADB 设备（请确认 Android Studio 模拟器已启动、adb 在 PATH）: {e}")
        return 2

    # LLM 网关预检：把"跑起来才发现的网络卡死"提前到这里，即时报错
    print("LLM 网关连通性预检（约 5~20 秒）...")
    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.api_key, base_url=settings.base_url, timeout=20)
        resp = client.chat.completions.create(
            model=settings.llm_model, messages=[{"role": "user", "content": "回复OK即可"}],
            max_tokens=8, timeout=20)
        print(f"网关正常，模型应答: {resp.choices[0].message.content[:30]}")
    except Exception as e:  # noqa: BLE001
        print(f"错误：LLM 网关不可达（检查 .env 密钥 / 网络 / 代理）: {e}")
        print("提示：若网络慢可在 .env 中把 VLM_TIMEOUT 调小（如 30）以缩短每次等待。")
        return 2

    suite = DEEP_SUITE if args.suite == "deep" else DEFAULT_SUITE
    if args.tasks:
        names = {x.strip() for x in args.tasks.split(",") if x.strip()}
        suite = [t for t in suite if t["case_id"] in names]
        if not suite:
            print("错误：--tasks 中没有匹配的任务。")
            return 2

    summary = run_batch(suite=suite, repeats=args.repeats, max_steps=args.max_steps,
                        rca_on_failure=args.rca_on_failure,
                        offline_rca=args.offline_rca, settings=settings)
    print("\n" + _to_markdown(summary))
    print(f"\n实测报告已保存: {summary['report_md']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
