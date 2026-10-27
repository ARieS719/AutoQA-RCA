# app.py —— AutoQA-RCA 统一控制台（Streamlit）
# 四个标签页：RCA 诊断 / UI 自动化测试 / 性能基准 / 报告归档
import os
import time
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent

st.set_page_config(page_title="AutoQA-RCA 车联网测试诊断闭环中台",
                   page_icon="🚗", layout="wide")

st.markdown("""
<style>
.report-box { padding: 20px; border-radius: 10px; background-color: #f0f2f6; color: #1e1e1e; }
</style>
""", unsafe_allow_html=True)

tab_rca, tab_ui, tab_perf, tab_reports = st.tabs(
    ["🚗 RCA 双轨诊断", "🧪 UI 自动化测试", "📈 性能基准", "📚 报告归档"])


def _import_ui_parser(incident):
    from rca_engine.evidence.ui_evidence import UIEvidenceParser
    return UIEvidenceParser(incident.base_dir)


# ============================================================
# Tab 1：RCA 双轨诊断
# ============================================================
@st.cache_data(show_spinner=False)
def list_demo_cases():
    from config.settings import settings
    from incident_bridge.evidence_collector import EvidenceCollector
    return [(str(c), EvidenceCollector.load_from_case_dir(c))
            for c in EvidenceCollector.list_demo_cases(settings.data_dir)]


with tab_rca:
    st.title("🚗 车联网异构日志 Multi-Agent 溯源系统（双轨定责）")
    st.markdown("> **定位**：UI 测试失败自动分诊——纯界面问题走轨A（视觉+Logcat），"
                "车联功能问题由证据门放行轨B（MQTT/T-BOX/CAN 三域对齐），双轨互为仲裁。")

    cases = list_demo_cases()
    if not cases:
        st.warning("未发现演示案例。请在命令行先执行：`python -m mock_data.fault_injection`")
        case_selector = None
    else:
        options = [c[0] for c in cases]
        labels = {c[0]: f"{c[1].case_id} — {c[1].title}" for c in cases}
        case_selector = st.selectbox("选择故障注入案例（答案卡在该案例目录的 ground_truth.md）",
                                     options=options, format_func=lambda x: labels[x])
        st.caption("先看 AI 报告，再看 ground_truth.md 对答案，体验 AI 溯源能力。")

    col1, col2 = st.columns(2)
    with col1:
        do_align = st.button("🔄 第一步：证据预览（离线，不耗 API）", use_container_width=True)
    with col2:
        do_diag = st.button("🚀 第二步：启动双轨诊断", type="primary", use_container_width=True)

    if (do_align or do_diag) and case_selector:
        from config.settings import settings
        from incident_bridge.evidence_collector import EvidenceCollector
        from incident_bridge.triage import Triage

        incident = EvidenceCollector.load_from_case_dir(case_selector)
        plan = Triage(settings.evidence_gate_patterns).decide(incident)
        st.subheader("失败分诊（证据门）")
        st.write(f"- 案例域：`{incident.domain}`｜链路依赖：`{incident.link_dependency or '无'}`")
        st.write(f"- 轨A（UI/座舱域）：{'✅ 启用' if plan.ui else '⏭ 跳过'}　"
                 f"轨B（车联网链路域）：{'✅ 启用' if plan.telematics else '⏭ 证据门拦截'}")
        for r in plan.reasons:
            st.write(f"　· {r}")

    if do_align and case_selector:
        from config.settings import settings
        from incident_bridge.evidence_collector import EvidenceCollector
        from rca_engine.evidence.telematics_evidence import TelematicsEvidenceParser
        incident = EvidenceCollector.load_from_case_dir(case_selector)
        st.subheader("确定性数据管道统计")
        if Path(incident.base_dir, "mqtt_cloud.log").exists():
            parser = TelematicsEvidenceParser(incident.base_dir,
                                              ignore_keywords=settings.ignore_keywords)
            t0, t1 = incident.time_window if incident.time_window else (None, None)
            text, stats = parser.align(t0, t1)
            st.code(stats.summary(), language=None)
            st.code(text[:3000], language="text")
        else:
            ui_parser = _import_ui_parser(incident)
            st.code(ui_parser.build_context()[:3000], language="text")

    if do_diag and case_selector:
        from config.settings import settings
        from incident_bridge.evidence_collector import EvidenceCollector
        from incident_bridge.triage import Triage
        from rca_engine.graph_pipeline import run_rca_pipeline
        from rca_engine.report_writer import ReportWriter

        incident = EvidenceCollector.load_from_case_dir(case_selector)
        plan = Triage(settings.evidence_gate_patterns).decide(incident)

        offline = not settings.api_key
        if offline:
            st.warning("未配置 SILICONFLOW_API_KEY，将运行离线确定性模式"
                       "（对齐/降噪/解码/分诊可用，定责叙述需 LLM）。")
        llm, rag = None, None
        if not offline:
            try:
                from rca_engine.graph_pipeline import (build_default_embeddings,
                                                       build_default_llm)
                from rca_engine.rag_store import RAGStore
                llm = build_default_llm(settings)
                rag = RAGStore(build_default_embeddings(settings),
                               settings.rag_persist_dir, settings.knowledge_file)
            except Exception as e:
                st.warning(f"LLM/RAG 初始化失败，降级离线模式：{e}")
                llm, rag, offline = None, None, True

        with st.spinner("AI 专家团协同推理中..."):
            result = run_rca_pipeline(incident, plan, llm=llm, rag=rag,
                                      writer=ReportWriter(settings.reports_dir),
                                      use_graph=True, offline=offline)
        stats = result.get("tlm_stats") or {}
        if stats:
            st.code(stats.get("summary", ""), language=None)
        final = result.get("final", {})
        st.success(f"诊断完成！仲裁结论：{final['arbitration'].get('note', '')}")
        st.markdown("<div class='report-box'>", unsafe_allow_html=True)
        from rca_engine.report_writer import ReportWriter as _RW
        st.markdown(_RW.to_markdown(incident.case_id, final))
        st.markdown("</div>", unsafe_allow_html=True)
        if result.get("report_md_path"):
            st.caption(f"报告已归档：{result['report_md_path']}")


# ============================================================
# Tab 2：UI 自动化测试（需 ADB 真机/模拟器）
# ============================================================
with tab_ui:
    st.title("🧪 纯视觉 UI 自动化（自然语言用例）")
    goal = st.text_area("测试目标（自然语言）",
                        value="打开系统的 Camera 应用，进入后任务完成。"
                              "若遇到权限弹窗请自主处理。")
    col1, col2 = st.columns(2)
    with col1:
        domain = st.selectbox("用例域", ["ui", "mixed"], index=0,
                              help="mixed=涉及车联功能，失败时证据门自动开启链路轨")
    with col2:
        link_deps = st.multiselect("链路依赖（domain=mixed 时）",
                                   ["cloud", "tbox", "can"], default=["tbox"])
    if st.button("▶ 运行用例", type="primary"):
        try:
            from config.settings import settings
            from ui_agent.adb_controller import AdbController
            from ui_agent.vlm_client import VLMClient
            from ui_agent.som_marker import SoMMarker
            from ui_agent.step_recorder import StepRecorder
            from ui_agent.agent_engine import AgentEngine
        except ImportError as e:
            st.error(f"依赖缺失：{e}")
            st.stop()
        if not settings.api_key:
            st.error("请先配置 SILICONFLOW_API_KEY（复制 .env.example 为 .env）")
            st.stop()
        adb = AdbController()
        try:
            adb.run_adb_cmd(["get-state"])
        except Exception:
            st.error("未检测到 ADB 设备。请连接车机/模拟器并确认 `adb devices` 可见。")
            st.stop()

        recorder = StepRecorder(settings.data_dir, "ui_manual_case")
        engine = AgentEngine(adb_controller=adb,
                             vlm_client=VLMClient(settings.api_key,
                                                  base_url=settings.base_url,
                                                  model_name=settings.vlm_model),
                             som_marker=SoMMarker(),
                             recorder=recorder,
                             settings=settings)
        deps = link_deps if domain == "mixed" else None
        with st.spinner("Agent 自主执行中（截图→打标→推理→操作→断言）..."):
            result = engine.execute_task(goal, case_id="ui_manual_case",
                                         domain=domain, link_dependency=deps)
        if result["success"]:
            st.success("用例通过！")
        else:
            st.error(f"用例失败：{result['note']}")
            st.caption(f"失败证据包已留存：{result['run_dir']}")
            st.caption("证据包可直接进入 Tab1 诊断：把该目录路径作为 --case 传入 CLI，"
                       "或等待平台版报告归档联动。")

# ============================================================
# Tab 3：性能基准
# ============================================================
with tab_perf:
    st.title("📈 视频流 E2E 性能基准（帧差法）")
    video_path = st.text_input("本地录屏文件路径（或使用 ADB 现场录制）",
                               value=str(PROJECT_ROOT / "perf_transition.mp4"))
    if st.button("🎬 分析转场耗时"):
        try:
            from ui_agent.performance_analyzer import PerformanceAnalyzer
        except ImportError as e:
            st.error(f"依赖缺失：{e}")
            st.stop()
        if not Path(video_path).exists():
            st.error(f"文件不存在：{video_path}")
        else:
            analyzer = PerformanceAnalyzer()
            with st.spinner("帧差分析中..."):
                seconds = analyzer.analyze_transition_time(video_path)
            if seconds >= 0:
                st.success(f"转场/冷启动耗时：{seconds:.3f} 秒")
            else:
                st.warning("未检测到完整转场生命周期")

# ============================================================
# Tab 4：报告归档
# ============================================================
with tab_reports:
    st.title("📚 RCA 报告归档")
    try:
        from config.settings import settings
        reports = sorted(settings.reports_dir.glob("*.md"), key=lambda p: p.stat().st_mtime,
                         reverse=True)
    except Exception:
        reports = []
    if not reports:
        st.info("暂无报告。运行诊断后自动归档到 reports/。")
    for rp in reports[:30]:
        with st.expander(rp.name):
            st.markdown(rp.read_text(encoding="utf-8"))
