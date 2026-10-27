"""真实数据接入工具：把任意来源的实车/台架日志打包成标准案例。

用途（P0"真实数据验证"的落地工具）：
把你们环境里采集的真实日志（MQTT 抓包 / T-BOX logcat 导出 / CAN 记录）
拷入标准案例目录并生成 case_meta.json，之后即可走与演示案例完全相同的
分诊 -> 双轨诊断链路。

用法：
  python -m mock_data.ingest_real_case --dir "D:\\logs\\20261020_ctrl_timeout" \
      --case-id case_real_001 --title "实车控车超时" --domain telematics \
      --window "2026-10-20 09:10:00,2026-10-20 09:12:30"

- 文件自动识别（按文件名关键词）：含 mqtt -> mqtt_cloud.log，
  含 logcat/tbox/android -> tbox_logcat.txt，含 can -> can_trace.csv；
- --domain ui/mixed 时可用 --steps 指定步迹 jsonl、--screenshots 指定截图目录；
- 不生成 ground_truth（真实故障没有"标准答案"），evaluate 会自动跳过；
- 真实数据建议脱敏后入库。
"""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

FILE_HINTS = {
    "mqtt_cloud.log": ("mqtt", "cloud", "tsp"),
    "tbox_logcat.txt": ("logcat", "tbox", "t-box", "android", "main_log"),
    "can_trace.csv": ("can", "trace"),
}


def _autodetect(source_dir: Path) -> dict:
    found = {}
    for f in source_dir.iterdir():
        if not f.is_file() or f.name.startswith("case_meta"):
            continue
        low = f.name.lower()
        for target, hints in FILE_HINTS.items():
            if target not in found and any(h in low for h in hints):
                found[target] = f
    return found


def build_real_case(source_dir, case_id: str, title: str, domain: str,
                    link_dependency, window, steps_file=None,
                    screenshots_dir=None, data_dir=None) -> Path:
    from config.settings import settings
    src = Path(source_dir)
    out_root = Path(data_dir) if data_dir else settings.data_dir / "real_cases"
    case_dir = out_root / case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    found = _autodetect(src)
    packaged = []
    for target, src_file in found.items():
        dst = case_dir / target
        shutil.copyfile(src_file, dst)
        packaged.append(f"{target} <- {src_file.name}")

    if steps_file:
        shutil.copyfile(steps_file, case_dir / "ui_steps.jsonl")
        packaged.append(f"ui_steps.jsonl <- {Path(steps_file).name}")
    if screenshots_dir:
        for p in sorted(Path(screenshots_dir).glob("*.png")):
            shutil.copyfile(p, case_dir / p.name)
        packaged.append(f"step_*.png <- {screenshots_dir}")

    meta = {
        "case_id": case_id,
        "title": title,
        "domain": domain,
        "link_dependency": list(link_dependency or []),
        "symptom": title,
        "source_note": "真实采集数据（ingest_real_case 打包）",
    }
    if window:
        try:
            t0, t1 = [datetime.fromisoformat(x.strip()) for x in window.split(",")]
            meta["time_window"] = [t0.isoformat(), t1.isoformat()]
        except Exception as e:
            print(f"警告：时间窗解析失败（忽略）：{e}")
    (case_dir / "case_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 70)
    print(f"真实案例已打包: {case_dir}")
    for p in packaged:
        print(f"  + {p}")
    if not packaged:
        print("  ! 未识别到任何日志文件，请确认目录内容或手动改名。")
    print("下一步（离线预检）:")
    print(f"  python -m rca_engine.cli diagnose --case {case_dir} --offline")
    print("完整 AI 诊断（需 .env 配置密钥）:")
    print(f"  python -m rca_engine.cli diagnose --case {case_dir}")
    print("=" * 70)
    return case_dir


def main() -> int:
    parser = argparse.ArgumentParser(prog="ingest_real_case", description="真实日志打包为案例")
    parser.add_argument("--dir", required=True, help="原始日志所在目录")
    parser.add_argument("--case-id", required=True, help="案例 ID（如 case_real_001）")
    parser.add_argument("--title", default="真实采集故障", help="案例标题")
    parser.add_argument("--domain", default="telematics", choices=["ui", "telematics", "mixed"])
    parser.add_argument("--link", default="", help="链路依赖，逗号分隔（如 cloud,tbox,can）")
    parser.add_argument("--window", default="", help='失败时间窗 "起始,结束"（ISO 格式，可选）')
    parser.add_argument("--steps", default="", help="ui_steps.jsonl 路径（domain=ui/mixed 时）")
    parser.add_argument("--screenshots", default="", help="截图目录（可选）")
    args = parser.parse_args()

    links = [x.strip() for x in args.link.split(",") if x.strip()]
    build_real_case(args.dir, args.case_id, args.title, args.domain, links,
                    args.window or None, args.steps or None,
                    args.screenshots or None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
