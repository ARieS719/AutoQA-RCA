"""LangGraph 编排：双轨定责流水线（轨A UI/座舱域 + 轨B 车联网链路域）。

设计要点：
1. 轨B 绝不因"轨A 没查到"而启动 —— 是否运行由 triage 的 TrackPlan
   决定（证据门：用例元数据 + 证据签名），本模块只执行计划；
2. 双轨并行取证，merge 节点做双向仲裁：轨B 证"链路正常且已应答"
   -> 责任回摆座舱应用层，轨A 收口；
3. 全部节点支持 LLM 注入（ChatOpenAI / 测试用 FakeChatModel），
   offline 模式走确定性规则产出证据报告，不消耗 API；
4. langgraph 缺失时降级为顺序执行，架构声明与实际行为一致。
"""
import json
import logging
import re
from typing import Dict, List, Optional, TypedDict

from .cost_tracker import CostTracker, TokenEstimator
from .dbc_tool import CanDecoder, extract_can_ids
from .report_writer import ReportWriter

logger = logging.getLogger(__name__)


# ============================================================
# 0. 状态定义与通用工具
# ============================================================
class PipelineState(TypedDict, total=False):
    """图状态：全部字段可选，节点返回局部更新。"""
    case_id: str
    title: str
    mode: str                      # "llm" | "offline"
    plan_ui: bool
    plan_telematics: bool
    triage_reasons: List[str]
    symptom: str
    ui_context: str
    tlm_text: str
    tlm_stats: dict
    ui_clues: str
    ui_rag: str
    ui_verdict: dict
    tlm_filtered: str
    tlm_decoded: str
    tlm_rag: str
    tlm_verdict: dict
    arbitration: dict
    final: dict
    report_json_path: str
    report_md_path: str


def _extract_json(text: str) -> dict:
    if not text:
        return {}
    try:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
    except Exception:
        pass
    return {}


def _call_llm(llm, messages: List[dict], tracker=None, node: str = "") -> str:
    """兼容 ChatOpenAI 与测试替身：统一返回文本内容；可选记录 token/成本。"""
    resp = llm.invoke(messages)
    content = getattr(resp, "content", "") or ""
    if tracker is not None:
        tracker.record(node or "llm", str(messages), content)
    return content


def _get_tool_calls(resp) -> List[dict]:
    tcs = getattr(resp, "tool_calls", None) or []
    out = []
    for tc in tcs:
        if isinstance(tc, dict):
            out.append(tc)
        else:
            out.append({"name": getattr(tc, "name", ""), "args": getattr(tc, "args", {}) or {}})
    return out


def make_can_tool(decoder: CanDecoder):
    """生成 LangChain Tool（惰性导入，未安装 langchain 时不影响模块加载）。"""
    from langchain_core.tools import tool

    @tool
    def decode_can_protocol(can_id: str) -> str:
        """当发现未知的 CAN ID（例如 0x3F2 或 0x0C1）时，调用此工具查询该 ID 的真实物理含义。
        参数 can_id 必须是十六进制字符串。"""
        return decoder.decode(can_id)

    return decode_can_protocol


def build_default_llm(settings):
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.api_key or None,
        base_url=settings.base_url or None,
        max_tokens=2048,
        temperature=0.1,
    )


def build_default_embeddings(settings):
    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.api_key or None,
        base_url=settings.base_url or None,
    )


# ============================================================
# 1. 图节点（每个节点只依赖 state，轨道未启用时立即空转）
# ============================================================
def node_gate(state: PipelineState) -> dict:
    plan = " + ".join(
        [("轨A/UI域" if state.get("plan_ui") else ""), ("轨B/车联域" if state.get("plan_telematics") else "")]
    ).strip(" +")
    logger.info("[gate] 案例 %s 执行计划: %s", state.get("case_id"), plan or "无")
    return {}


def node_ui_purify(llm=None, tracker=None) -> callable:
    def _fn(state: PipelineState) -> dict:
        if not state.get("plan_ui"):
            return {}
        ctx = state.get("ui_context", "")
        if state.get("mode") == "offline":
            return {"ui_clues": "【离线模式｜确定性证据】\n" + ctx[:2000]}
        messages = [
            {"role": "system", "content": (
                "你是资深座舱 UI 测试开发专家。基于以下执行步迹、应用层异常签名与 Logcat 尾段，"
                "提取本次 UI 失败的关键线索：失败发生在哪一步、应用层有无崩溃/ANR/渲染异常、"
                "是否存在与网络/链路无关的纯逻辑缺陷。输出精炼的中文线索摘要。"
            )},
            {"role": "user", "content": ctx},
        ]
        return {"ui_clues": _call_llm(llm, messages, tracker, "ui_purify")}
    return _fn


def node_ui_rag(rag=None) -> callable:
    def _fn(state: PipelineState) -> dict:
        if not state.get("plan_ui"):
            return {}
        clues = state.get("ui_clues", "")
        # k=4：提高召回（后续 LLM 自会甄别相关性；此前 k=2 漏掉过精确匹配的旧病记录）
        hits = rag.search(clues, k=4, domain="座舱") if rag else []
        if hits:
            # document 自带 "[Jira-xxxx] [域:xx]" 头，直接展示避免重复前缀
            text = "\n".join(f"- {h['document'][:300]}" for h in hits)
            return {"ui_rag": "检索到相似历史座舱缺陷：\n" + text}
        return {"ui_rag": "未检索到相关历史座舱缺陷。"}
    return _fn


def node_ui_judge(llm=None, tracker=None) -> callable:
    def _fn(state: PipelineState) -> dict:
        if not state.get("plan_ui"):
            return {}
        clues, rag = state.get("ui_clues", ""), state.get("ui_rag", "")
        if state.get("mode") == "offline":
            verdict = {
                "root_cause": "离线模式：仅输出确定性签名（见 clues），完整定性需 LLM。",
                "responsibility": "座舱应用团队（待 LLM 复核）",
                "confidence": "low",
                "actions": ["配置 SILICONFLOW_API_KEY 后重跑以获得完整定性"],
            }
            return {"ui_verdict": verdict}
        messages = [
            {"role": "system", "content": (
                "你是权威的座舱 UI 缺陷裁判。综合线索与历史缺陷，输出严格 JSON："
                '{"root_cause": "...", "responsibility": "...", "confidence": "high|medium|low", '
                '"actions": ["...", "..."]}'
            )},
            {"role": "user", "content": f"线索：\n{clues}\n\n历史溯源：\n{rag}"},
        ]
        return {"ui_verdict": _extract_json(_call_llm(llm, messages, tracker, "ui_judge"))}
    return _fn


def node_tlm_purify(llm=None, tracker=None) -> callable:
    def _fn(state: PipelineState) -> dict:
        if not state.get("plan_telematics"):
            return {}
        text = state.get("tlm_text", "")
        if state.get("mode") == "offline":
            # 确定性提纯：只保留错误级信号行
            kept = [ln for ln in text.splitlines()
                    if re.search(r"(?i)(error|timeout|fault|fail|deadlock)", ln)]
            return {"tlm_filtered": "\n".join(kept[:200]) or "(无错误级信号行)"}
        messages = [
            {"role": "system", "content": (
                "你是资深车联网测试开发专家。从异构日志中提取异常线索："
                "1. 忽略执行成功的业务链路（干扰项），死磕最终导致报错的那一次操作；"
                "2. 提取指令下发、车机接收、CAN 发送/反馈的关键时间节点；"
                "3. 绝不遗漏 TBOX_LOGCAT 的 Timeout；4. 列出异常期间的 CAN ID 列表。"
            )},
            {"role": "user", "content": f"原始日志：\n{text}"},
        ]
        return {"tlm_filtered": _call_llm(llm, messages, tracker, "tlm_purify")}
    return _fn


def node_tlm_decode(llm=None, decoder=None, tracker=None) -> callable:
    def _fn(state: PipelineState) -> dict:
        if not state.get("plan_telematics"):
            return {}
        filtered = state.get("tlm_filtered", "")
        if state.get("mode") == "offline":
            return {"tlm_decoded": decoder.decode_many(extract_can_ids(filtered))}
        try:
            from langchain_core.tools import tool  # noqa: F401
            llm_with_tools = llm.bind_tools([make_can_tool(decoder)])
        except Exception as e:
            logger.warning("bind_tools 不可用，降级为规则解码: %s", e)
            return {"tlm_decoded": decoder.decode_many(extract_can_ids(filtered))}

        messages: List[dict] = [
            {"role": "system", "content":
                "找出线索中的全部十六进制 CAN ID，并必须调用 decode_can_protocol 工具逐一查询其物理含义。"},
            {"role": "user", "content": f"提纯线索：\n{filtered}"},
        ]
        decoded: List[str] = []
        for _round in range(2):  # 多轮 Tool-Use（修复 P1-3）
            resp = llm_with_tools.invoke(messages)
            if tracker is not None:
                tracker.record("tlm_decode", str(messages), getattr(resp, "content", "") or "")
            tcs = _get_tool_calls(resp)
            if not tcs:
                break
            results = []
            for tc in tcs:
                can_id = (tc.get("args") or {}).get("can_id", "")
                if can_id:
                    out = decoder.decode(can_id)
                    decoded.append(f"ID {can_id} 解码为: {out}")
                    results.append(f"ID {can_id} -> {out}")
            messages.append({"role": "assistant", "content": getattr(resp, "content", "") or ""})
            messages.append({"role": "user", "content":
                             "工具返回：\n" + "\n".join(results) +
                             "\n若还有未解码的 CAN ID 请继续调用工具，否则请总结解码结论。"})
        if decoded:
            summary = _call_llm(llm, messages + [{"role": "user", "content": "请总结以上解码结论。"}],
                                tracker, "tlm_decode")
            return {"tlm_decoded": "\n".join(decoded) + "\n\n总结：" + summary}
        return {"tlm_decoded": "未调用工具或未发现需要解码的 CAN ID。"}
    return _fn


def node_tlm_rag(rag=None) -> callable:
    def _fn(state: PipelineState) -> dict:
        if not state.get("plan_telematics"):
            return {}
        query = (state.get("tlm_filtered", "") + "\n" + state.get("tlm_decoded", ""))[:4000]
        hits = rag.search(query, k=4, domain="车联") if rag else []
        if hits:
            text = "\n".join(f"- {h['document'][:300]}" for h in hits)
            return {"tlm_rag": "检索到相似历史车联缺陷：\n" + text}
        return {"tlm_rag": "未检索到相关历史车联缺陷。"}
    return _fn


def node_tlm_judge(llm=None, tracker=None) -> callable:
    def _fn(state: PipelineState) -> dict:
        if not state.get("plan_telematics"):
            return {}
        filtered, decoded, rag = (state.get("tlm_filtered", ""),
                                  state.get("tlm_decoded", ""),
                                  state.get("tlm_rag", ""))
        if state.get("mode") == "offline":
            verdict = {
                "root_cause": "离线模式：仅输出确定性证据（对齐/解码结果），完整定性需 LLM。",
                "responsibility": "待定",
                "link_healthy": None,
                "confidence": "low",
                "actions": ["配置 SILICONFLOW_API_KEY 后重跑以获得完整定责"],
            }
            return {"tlm_verdict": verdict}
        messages = [
            {"role": "system", "content": (
                "你是权威的汽车测试架构师。综合提纯线索、协议解码与历史缺陷，输出严格 JSON："
                '{"root_cause": "...", "responsibility": "...", '
                '"link_healthy": true/false（true=链路层各环节均正常应答，问题在应用层）'
                ', "confidence": "high|medium|low", "actions": ["...", "..."]}'
            )},
            {"role": "user", "content":
                f"提纯线索：\n{filtered}\n\n协议解码：\n{decoded}\n\n历史溯源：\n{rag}"},
        ]
        return {"tlm_verdict": _extract_json(_call_llm(llm, messages, tracker, "tlm_judge"))}
    return _fn


def node_merge(state: PipelineState) -> dict:
    """双向仲裁（纯 Python，确定性可测）。"""
    plan_ui = bool(state.get("plan_ui"))
    plan_tlm = bool(state.get("plan_telematics"))
    tlm_v = state.get("tlm_verdict") or {}
    arb: Dict[str, str] = {"note": "未启用双轨：单一域失败，无交叉验证。", "direction": "single"}
    if plan_ui and plan_tlm:
        link_ok = tlm_v.get("link_healthy")
        if link_ok is True:
            arb = {
                "note": "轨B 证实链路完整闭环（指令送达、总线应答、状态回传均成功）——"
                        "链路无责，责任回摆座舱应用层，由轨A 收口定责。",
                "direction": "telematics->ui",
            }
        elif link_ok is False:
            arb = {
                "note": "轨B 定位到链路层根因，轨A 结论仅作为现象描述，最终责任以轨B 为准。",
                "direction": "telematics",
            }
        else:
            arb = {
                "note": "轨B 未能确定链路健康度，双轨结论并存，建议人工复核。",
                "direction": "manual",
            }
    return {"arbitration": arb}


def node_report(writer: Optional[ReportWriter] = None, tracker: Optional[CostTracker] = None) -> callable:
    def _fn(state: PipelineState) -> dict:
        arb = state.get("arbitration", {})
        ui_v = state.get("ui_verdict") or {}
        tlm_v = state.get("tlm_verdict") or {}
        direction = arb.get("direction", "single")

        if direction == "telematics->ui":
            root_cause = ui_v.get("root_cause", "")
            responsibility = (ui_v.get("responsibility", "") or "座舱应用层")
            actions = ui_v.get("actions", [])
            confidence = ui_v.get("confidence", "medium")
            rag_evidence = state.get("ui_rag", "-")
        elif state.get("plan_telematics"):
            root_cause = tlm_v.get("root_cause", "")
            responsibility = tlm_v.get("responsibility", "")
            actions = tlm_v.get("actions", [])
            confidence = tlm_v.get("confidence", "medium")
            rag_evidence = state.get("tlm_rag", "-")
        else:
            root_cause = ui_v.get("root_cause", "")
            responsibility = ui_v.get("responsibility", "")
            actions = ui_v.get("actions", [])
            confidence = ui_v.get("confidence", "medium")
            rag_evidence = state.get("ui_rag", "-")

        final = {
            "case_id": state.get("case_id"),
            "title": state.get("title", ""),
            "mode": state.get("mode", "llm"),
            "triage": {"ui": state.get("plan_ui"), "telematics": state.get("plan_telematics"),
                       "reasons": state.get("triage_reasons", [])},
            "symptom": state.get("symptom", "-"),
            "arbitration": arb,
            "final": {
                "root_cause": root_cause or "未得出",
                "responsibility": responsibility or "未得出",
                "confidence": confidence,
                "actions": actions,
                "rag_evidence": rag_evidence,
            },
            "ui_track": state.get("ui_clues", ""),
            "telematics_track": state.get("tlm_decoded", ""),
            "align_stats": state.get("tlm_stats", {}),
            "cost": {},
        }
        # ---- 成本面板：LLM 用量 + 降噪节省（估算口径见 cost_tracker 文档）----
        if tracker is not None:
            savings = {}
            stats = state.get("tlm_stats") or {}
            tlm_text = state.get("tlm_text") or ""
            lines = [ln for ln in tlm_text.splitlines() if ln.strip()]
            avg = (sum(TokenEstimator.estimate(ln) for ln in lines) / len(lines)) if lines else 15.0
            if stats.get("dropped_noise", 0) > 0:
                savings = tracker.noise_savings(stats["dropped_noise"], avg)
            final["cost"] = tracker.summary(savings)
        paths = {"final": final, "report_json_path": "", "report_md_path": ""}
        if writer is not None:
            try:
                jp, mp = writer.write(state.get("case_id", "case"), final)
                paths["report_json_path"], paths["report_md_path"] = jp, mp
            except Exception as e:
                logger.warning("报告落盘失败: %s", e)
        return paths
    return _fn


# ============================================================
# 2. 组装与入口
# ============================================================
def _compile_graph(llm, rag, writer, tracker=None):
    from langgraph.graph import StateGraph, END
    try:
        from langgraph.graph import START
        _has_start = True
    except Exception:
        _has_start = False

    g = StateGraph(PipelineState)
    g.add_node("gate", node_gate)
    g.add_node("ui_purify", node_ui_purify(llm, tracker))
    # 注意：langgraph 0.2.x 禁止节点名与状态键同名（'ui_rag' 冲突），故加 _node 后缀
    g.add_node("ui_rag_node", node_ui_rag(rag))
    g.add_node("ui_judge", node_ui_judge(llm, tracker))
    g.add_node("tlm_purify", node_tlm_purify(llm, tracker))
    g.add_node("tlm_decode", node_tlm_decode(llm, decoder=CanDecoder(), tracker=tracker))
    g.add_node("tlm_rag_node", node_tlm_rag(rag))
    g.add_node("tlm_judge", node_tlm_judge(llm, tracker))
    g.add_node("merge", node_merge)
    g.add_node("report", node_report(writer, tracker))

    if _has_start:
        g.add_edge(START, "gate")
    else:
        g.set_entry_point("gate")
    g.add_edge("gate", "ui_purify")
    g.add_edge("gate", "tlm_purify")
    g.add_edge("ui_purify", "ui_rag_node")
    g.add_edge("ui_rag_node", "ui_judge")
    g.add_edge("ui_judge", "merge")
    g.add_edge("tlm_purify", "tlm_decode")
    g.add_edge("tlm_decode", "tlm_rag_node")
    g.add_edge("tlm_rag_node", "tlm_judge")
    g.add_edge("tlm_judge", "merge")
    g.add_edge("merge", "report")
    g.add_edge("report", END)
    return g.compile()


def run_rca_pipeline(incident, plan, llm=None, rag=None, writer=None,
                     use_graph: bool = True, offline: bool = False,
                     tracker: Optional[CostTracker] = None) -> dict:
    """双轨 RCA 主入口。

    Args:
        incident: Incident（见 incident_bridge.evidence_collector）
        plan: TrackPlan（见 incident_bridge.triage）
        llm: 可注入的 LLM（默认 None 时调用方需自行传入；CLI 中由 build_default_llm 提供）
        rag: 可注入的 RAGStore / FakeRAGStore
        writer: ReportWriter
        use_graph: False 时降级为顺序执行（langgraph 缺失时自动降级）
        offline: True 时全程不调用 LLM，仅产出确定性证据报告
        tracker: 可注入的 CostTracker（None 时按 config 单价自动创建）
    """
    if not offline and llm is None:
        raise ValueError("LLM 模式需要注入 llm（CLI/控制台由 build_default_llm 提供）；"
                         "无密钥时请使用 offline=True")
    if tracker is None:
        from config.settings import settings as _settings
        tracker = CostTracker(input_price_per_1m=_settings.llm_input_price_per_1m,
                              output_price_per_1m=_settings.llm_output_price_per_1m)
    tlm_text, tlm_stats = "", {}
    ui_context = ""
    if plan.telematics:
        from .evidence.telematics_evidence import TelematicsEvidenceParser
        from config.settings import settings as _settings
        parser = TelematicsEvidenceParser(
            incident.base_dir, ignore_keywords=_settings.ignore_keywords)
        t0, t1 = incident.time_window if incident.time_window else (None, None)
        tlm_text, stats = parser.align(t0, t1)
        tlm_stats = {
            "parsed": stats.parsed, "dropped_noise": stats.dropped_noise,
            "kept": stats.kept, "summary": stats.summary(),
        }
    if plan.ui:
        from .evidence.ui_evidence import UIEvidenceParser
        ui_parser = UIEvidenceParser(incident.base_dir)
        ui_context = ui_parser.build_context()

    init = PipelineState(
        case_id=incident.case_id,
        title=incident.title,
        mode="offline" if offline else "llm",
        plan_ui=plan.ui,
        plan_telematics=plan.telematics,
        triage_reasons=list(plan.reasons),
        symptom=incident.symptom,
        ui_context=ui_context,
        tlm_text=tlm_text,
        tlm_stats=tlm_stats,
    )

    if use_graph:
        try:
            graph = _compile_graph(llm, rag, writer, tracker)
            result = graph.invoke(init)
            return result
        except ImportError:
            logger.warning("langgraph 不可用，降级为顺序执行模式")
        except Exception as e:
            logger.error("LangGraph 执行失败，降级为顺序执行: %s", e)

    # ---- 顺序降级（行为与图等价） ----
    state = dict(init)
    state.update(node_gate(state))
    state.update(node_ui_purify(llm, tracker)(state))
    state.update(node_ui_rag(rag)(state))
    state.update(node_ui_judge(llm, tracker)(state))
    state.update(node_tlm_purify(llm, tracker)(state))
    state.update(node_tlm_decode(llm, decoder=CanDecoder(), tracker=tracker)(state))
    state.update(node_tlm_rag(rag)(state))
    state.update(node_tlm_judge(llm, tracker)(state))
    state.update(node_merge(state))
    state.update(node_report(writer, tracker)(state))
    return state
