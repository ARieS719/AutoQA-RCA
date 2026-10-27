"""AutoQA-RCA 命令行入口。

用法：
  python -m rca_engine.cli cases
  python -m rca_engine.cli diagnose --case data_output/demo_cases/case_2_can_congestion
  python -m rca_engine.cli diagnose --case data_output/demo_cases/case_1_ui_crash --offline
"""
import argparse
import logging
import sys
from pathlib import Path

# 允许以 `python rca_engine/cli.py` 方式直接运行
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - [%(module)s] - %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_cases(settings) -> None:
    from incident_bridge.evidence_collector import EvidenceCollector
    cases = EvidenceCollector.list_demo_cases(settings.data_dir)
    if not cases:
        print("未发现演示案例。请先运行: python -m mock_data.fault_injection")
        return
    print("=" * 60)
    print("可用演示案例（故障注入集）:")
    for c in cases:
        meta = EvidenceCollector.load_from_case_dir(c)
        print(f"  - {c.name:35s} 域={meta.domain:12s}  {meta.title}")
    print("=" * 60)


def _build_llm_rag(settings, offline: bool):
    """按模式构建 LLM/RAG；初始化失败自动降级离线。返回 (llm, rag, offline)。"""
    if offline:
        return None, None, True
    from rca_engine.graph_pipeline import build_default_embeddings, build_default_llm
    from rca_engine.rag_store import RAGStore
    try:
        llm = build_default_llm(settings)
        rag = RAGStore(build_default_embeddings(settings),
                       settings.rag_persist_dir, settings.knowledge_file)
        return llm, rag, False
    except Exception as e:
        print(f"⚠ LLM/RAG 初始化失败，降级离线模式: {e}")
        return None, None, True


def cmd_diagnose(args, settings) -> int:
    from incident_bridge.evidence_collector import EvidenceCollector
    from incident_bridge.triage import Triage
    from rca_engine.graph_pipeline import run_rca_pipeline
    from rca_engine.report_writer import ReportWriter

    case_path = Path(args.case)
    if not (case_path / "case_meta.json").exists():
        print(f"错误：{case_path} 不是有效案例目录（缺少 case_meta.json）")
        return 2

    incident = EvidenceCollector.load_from_case_dir(case_path)
    plan = Triage(settings.evidence_gate_patterns).decide(incident)

    print("=" * 70)
    print(f"案例: {incident.title}")
    print(f"域: {incident.domain} | 链路依赖: {incident.link_dependency or '无'}")
    print(f"分诊结论: 轨A(UI域)={'√' if plan.ui else '×'} 轨B(车联域)={'√' if plan.telematics else '×'}")
    for r in plan.reasons:
        print(f"   依据: {r}")
    print("=" * 70)

    offline = bool(args.offline) or not settings.api_key
    if offline and not settings.api_key:
        print("⚠ 未配置 SILICONFLOW_API_KEY，自动切换离线确定性模式"
              "（对齐/降噪/解码/分诊可用，定责叙述需 LLM）。")
    elif offline:
        print("⚙ 已指定 --offline，本次不调用 LLM。")

    llm, rag, offline = _build_llm_rag(settings, offline)

    writer = ReportWriter(settings.reports_dir)
    result = run_rca_pipeline(
        incident, plan, llm=llm, rag=rag, writer=writer,
        use_graph=not args.no_graph, offline=offline,
    )

    stats = result.get("tlm_stats") or {}
    if stats:
        print("\n[确定性数据管道统计] " + stats.get("summary", ""))
    if result.get("ui_track"):
        print("\n[轨A 线索摘录]\n" + str(result["ui_track"])[:600])
    if result.get("telematics_track"):
        print("\n[轨B 解码摘录]\n" + str(result["telematics_track"])[:600])

    final = result.get("final", {})
    print("\n" + "=" * 70)
    print("最终结论:")
    print(f"  根因: {final['final'].get('root_cause', '-')}")
    print(f"  责任方: {final['final'].get('responsibility', '-')}")
    print(f"  仲裁: {final['arbitration'].get('note', '-')}")
    print(f"  置信度: {final['final'].get('confidence', '-')}")
    if result.get("report_md_path"):
        print(f"\n完整报告已保存: {result['report_md_path']}")
    cost = final.get("cost") or {}
    if cost:
        ns = cost.get("noise_savings") or {}
        print("\n[成本面板｜估算]")
        print(f"  LLM 调用 {cost.get('llm_calls', 0)} 次 | 输入 ≈{cost.get('est_prompt_tokens', 0)} token | "
              f"输出 ≈{cost.get('est_completion_tokens', 0)} token | 成本 ≈ ¥{cost.get('est_cost_cny', 0)}")
        if ns:
            print(f"  确定性降噪节省输入 ≈{ns.get('est_input_tokens_saved', 0)} token"
                  f"（≈ ¥{ns.get('est_input_cost_saved_cny', 0)}）")
    print("=" * 70)
    return 0


def cmd_evaluate(args, settings) -> int:
    from rca_engine.evaluate import discover_cases, print_table, run_benchmark

    cases = discover_cases(settings.data_dir)
    if args.cases:
        names = set(args.cases)
        cases = [c for c in cases if c.name in names]
    if not cases:
        print("未发现带 case_meta.json 的案例目录。请先运行 python -m mock_data.fault_injection")
        return 2

    offline = bool(args.offline) or not settings.api_key
    if offline and not settings.api_key:
        print("⚠ 未配置 SILICONFLOW_API_KEY：根因/责任/RAG/仲裁指标将标记 skipped（仅分诊可测）。")
    llm, rag, offline = _build_llm_rag(settings, offline)

    print(f"评测案例（{len(cases)} 个，repeat={args.repeat}，mode={'offline' if offline else 'llm'}）：")
    for c in cases:
        print(f"  - {c.name}")
    summary = run_benchmark(cases, repeat=args.repeat, offline=offline,
                            llm=llm, rag=rag, settings=settings,
                            reports_dir=settings.reports_dir)
    print_table(summary)
    if summary.get("report_md"):
        print(f"\n评测报告已保存: {summary['report_md']}")
    return 0


def main(argv=None) -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(prog="rca_engine", description="AutoQA-RCA 双轨定责引擎")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("cases", help="列出全部演示案例")
    p = sub.add_parser("diagnose", help="对指定案例执行分诊与双轨诊断")
    p.add_argument("--case", required=True, help="案例目录路径（含 case_meta.json）")
    p.add_argument("--offline", action="store_true", help="离线确定性模式（不调用 LLM）")
    p.add_argument("--no-graph", action="store_true", help="禁用 LangGraph（顺序降级执行）")
    pe = sub.add_parser("evaluate", help="评测基准：AI 判定 vs ground_truth 自动比对")
    pe.add_argument("--repeat", type=int, default=1, help="每案例重复运行次数（测稳定性）")
    pe.add_argument("--offline", action="store_true", help="离线模式（仅分诊指标可测）")
    pe.add_argument("--cases", nargs="*", default=None, help="只评测指定案例名（可选）")
    args = parser.parse_args(argv)

    # 延迟导入 settings，避免启动即读 .env
    from config.settings import settings

    if args.cmd == "cases":
        cmd_cases(settings)
        return 0
    if args.cmd == "evaluate":
        return cmd_evaluate(args, settings)
    return cmd_diagnose(args, settings)


if __name__ == "__main__":
    sys.exit(main())
