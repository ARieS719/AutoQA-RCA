"""结构化报告双产物：JSON（供程序消费）+ Markdown（供人阅读）。

统一 schema，无论走轨A / 轨B / 双轨仲裁，产出结构一致，
便于归档、检索与后续"修复建议 -> 回归用例"的自动流转。
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple


class ReportWriter:
    def __init__(self, reports_dir):
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def write(self, case_id: str, final: dict) -> Tuple[str, str]:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = f"{case_id}_{ts}"
        json_path = self.reports_dir / f"{stem}.json"
        md_path = self.reports_dir / f"{stem}.md"
        json_path.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
        md_path.write_text(self.to_markdown(case_id, final), encoding="utf-8")
        return str(json_path), str(md_path)

    @staticmethod
    def to_markdown(case_id: str, final: dict) -> str:
        f = final
        triage = f.get("triage", {})
        arb = f.get("arbitration", {})
        verdict = f.get("final", {})
        lines = [
            f"# RCA 报告：{f.get('title', case_id)}",
            "",
            f"- 案例 ID：`{case_id}`",
            f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 运行模式：{f.get('mode', 'llm')}",
            f"- 分诊结论：UI轨={'是' if triage.get('ui') else '否'}，"
            f"车联轨={'是' if triage.get('telematics') else '否'}（依据：{'；'.join(triage.get('reasons', [])) or '无'}）",
            "",
            "## 1. 故障现象 (What)",
            f.get("symptom", "-"),
            "",
            "## 2. 根因定性 (Why)",
            verdict.get("root_cause", "-"),
            "",
            "## 3. 责任判定 (Who)",
            verdict.get("responsibility", "-"),
            "",
            "## 4. 仲裁说明（双轨交叉验证）",
            arb.get("note", "本案例未启用双轨（纯单一域失败）"),
            "",
            "## 5. 历史溯源 (History)",
            verdict.get("rag_evidence", "-"),
            "",
            "## 6. 修复建议 (Action)",
            "\n".join(f"- {a}" for a in verdict.get("actions", [])) or "-",
            "",
            "## 7. 置信度",
            f"`{verdict.get('confidence', '-')}`",
        ]
        # 成本面板（估算口径）
        cost = f.get("cost") or {}
        if cost:
            ns = cost.get("noise_savings") or {}
            lines += ["", "## 8. 成本与降噪收益（估算）",
                      f"- LLM 调用 {cost.get('llm_calls', 0)} 次；"
                      f"输入 ≈{cost.get('est_prompt_tokens', 0)} token，"
                      f"输出 ≈{cost.get('est_completion_tokens', 0)} token；"
                      f"估算成本 ≈ ¥{cost.get('est_cost_cny', 0)}"]
            if ns:
                lines.append(
                    f"- 确定性降噪：剔除 {ns.get('noise_lines_filtered', 0)} 行噪音，"
                    f"估算节省输入 ≈{ns.get('est_input_tokens_saved', 0)} token"
                    f"（≈ ¥{ns.get('est_input_cost_saved_cny', 0)}）")
            lines.append(f"- 口径说明：{cost.get('note', '')}")
        # 双轨详细产出（如有）
        for key, title in (("ui_track", "轨A｜UI/座舱域详细产出"),
                           ("telematics_track", "轨B｜车联网链路域详细产出")):
            detail = f.get(key)
            if detail:
                lines += ["", f"## 附：{title}", str(detail)]
        return "\n".join(lines)
