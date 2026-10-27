"""自动化评测基准：AI 判定 vs ground_truth 自动比对（把"声称"变成"测量"）。

指标（诚实口径）：
- triage_accuracy       分诊轨道（轨A/轨B 启停）与真相一致率（离线可测）
- root_cause_hit_rate   最终根因命中真相关键词（需 LLM 模式，否则 skipped）
- responsibility_hit_rate 责任方命中真相关键词（同上）
- rag_hit_rate          检索证据中出现预期 Jira 号（需 RAG）
- arbitration_accuracy  仲裁方向与真相一致（双轨案例核心指标）

用法：
  python -m rca_engine.cli evaluate [--repeat N] [--offline] [--cases name1 name2 ...]
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def discover_cases(data_dir) -> List[Path]:
    """收集全部带 case_meta.json 的案例目录（demo_cases 与经典 case_*）。"""
    data_dir = Path(data_dir)
    roots = [data_dir / "demo_cases", data_dir]
    seen, out = set(), []
    for root in roots:
        if not root.exists():
            continue
        for p in sorted(root.iterdir()):
            if not p.is_dir() or p.name == "demo_cases":
                continue
            key = str(p.resolve())
            if key not in seen and (p / "case_meta.json").exists():
                seen.add(key)
                out.append(p)
    return out


def _hit(text: str, keywords: List[str]) -> bool:
    if not keywords:
        return True
    t = (text or "").lower()
    return any(k.lower() in t for k in keywords)


def score_one(result: dict, truth: dict, offline: bool) -> dict:
    """单个案例单次运行 vs 真相卡，输出逐指标结果。"""
    final = result.get("final") or {}
    triage = final.get("triage") or {}
    verdict = final.get("final") or {}
    arb = final.get("arbitration") or {}
    exp_track = truth.get("expected_track") or {}

    row = {
        "triage": {
            "expected": exp_track,
            "actual": {"ui": bool(triage.get("ui")),
                       "telematics": bool(triage.get("telematics"))},
            "match": (bool(triage.get("ui")) == bool(exp_track.get("ui"))
                      and bool(triage.get("telematics")) == bool(exp_track.get("telematics"))),
        },
    }
    if offline:
        row.update({"root_cause": "skipped", "responsibility": "skipped",
                    "rag": "skipped", "arbitration": "skipped"})
        return row

    row["root_cause"] = {
        "hit": _hit(verdict.get("root_cause"), truth.get("root_cause_keywords")),
        "actual": str(verdict.get("root_cause", ""))[:200],
    }
    row["responsibility"] = {
        "hit": _hit(verdict.get("responsibility"), truth.get("responsibility_keywords")),
        "actual": str(verdict.get("responsibility", ""))[:100],
    }
    expected_rag = truth.get("rag_hit") or ""
    row["rag"] = {
        "hit": (expected_rag in (verdict.get("rag_evidence") or "")) if expected_rag else True,
        "expected": expected_rag,
    }
    row["arbitration"] = {
        "expected": truth.get("arbitration_direction"),
        "actual": arb.get("direction"),
        "match": arb.get("direction") == truth.get("arbitration_direction"),
    }
    return row


def _rate(rows: List[dict], key: str) -> Optional[float]:
    """计算某指标的命中率（skipped 不计入分母；无有效样本返回 None）。

    兼容两种判定键：triage 用 match，其余指标用 hit。
    """
    vals = [r[key] for r in rows if isinstance(r.get(key), dict)]
    hits = [v for v in vals if (v.get("hit") is True) or (v.get("match") is True)]
    if not vals:
        return None
    return len(hits) / len(vals)   # 精确浮点值；展示层自行格式化


def run_benchmark(cases: List[Path], repeat: int = 1, offline: bool = False,
                  llm=None, rag=None, settings=None, reports_dir=None) -> dict:
    from incident_bridge.evidence_collector import EvidenceCollector
    from incident_bridge.triage import Triage
    from mock_data.truth_schema import load_truth
    from rca_engine.graph_pipeline import run_rca_pipeline

    triage = Triage(settings.evidence_gate_patterns)
    rows: List[dict] = []
    skipped: List[str] = []

    for case_dir in cases:
        truth = load_truth(case_dir)
        if truth is None:
            skipped.append(case_dir.name)
            continue
        incident = EvidenceCollector.load_from_case_dir(case_dir)
        plan = triage.decide(incident)
        for r in range(repeat):
            result = run_rca_pipeline(incident, plan, llm=llm, rag=rag, writer=None,
                                      use_graph=True, offline=offline)
            row = score_one(result, truth, offline)
            row.update({"case": case_dir.name, "run": r + 1,
                        "mode": "offline" if offline else "llm"})
            rows.append(row)

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "offline" if offline else "llm",
        "cases_scored": len({r["case"] for r in rows}),
        "total_runs": len(rows),
        "skipped_no_truth": skipped,
        "metrics": {
            "triage_accuracy": _rate(rows, "triage"),
            "root_cause_hit_rate": _rate(rows, "root_cause"),
            "responsibility_hit_rate": _rate(rows, "responsibility"),
            "rag_hit_rate": _rate(rows, "rag"),
            "arbitration_accuracy": _rate(rows, "arbitration"),
        },
        "rows": rows,
    }
    if reports_dir:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = Path(reports_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"evaluation_{ts}.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / f"evaluation_{ts}.md").write_text(to_markdown(summary), encoding="utf-8")
        summary["report_md"] = str(out / f"evaluation_{ts}.md")
    return summary


def to_markdown(s: dict) -> str:
    lines = [
        f"# 评测基准报告（{s['mode']} 模式）",
        f"- 生成时间：{s['generated_at']}",
        f"- 覆盖案例：{s['cases_scored']} 个 / 共 {s['total_runs']} 次运行",
        f"- 无真相卡跳过：{s['skipped_no_truth'] or '无'}",
        "",
        "## 总体指标",
        "| 指标 | 命中率 |",
        "|---|---|",
    ]
    for name, key in (("分诊准确率", "triage_accuracy"),
                      ("根因命中率", "root_cause_hit_rate"),
                      ("责任方命中率", "responsibility_hit_rate"),
                      ("RAG 命中率", "rag_hit_rate"),
                      ("仲裁正确率", "arbitration_accuracy")):
        v = s["metrics"].get(key)
        lines.append(f"| {name} | {f'{v:.1%}' if v is not None else 'skipped（离线模式不可测）'} |")
    lines += ["", "## 明细", "| 案例 | 轮 | 分诊 | 根因 | 责任 | RAG | 仲裁 |", "|---|---|---|---|---|---|---|"]
    for r in s["rows"]:
        def cell(key):
            v = r.get(key)
            if v == "skipped":
                return "—"
            if isinstance(v, dict) and "match" in v:
                return "✅" if v["match"] else "❌"
            if isinstance(v, dict) and "hit" in v:
                return "✅" if v["hit"] else "❌"
            return "?"
        lines.append(f"| {r['case']} | {r['run']} | {cell('triage')} | {cell('root_cause')} | "
                     f"{cell('responsibility')} | {cell('rag')} | {cell('arbitration')} |")
    lines.append("")
    lines.append("> 口径：命中=关键词包含判定（保守）；离线模式仅分诊可测；token/费用为估算。")
    return "\n".join(lines)


def print_table(s: dict) -> None:
    print("\n" + to_markdown(s))
