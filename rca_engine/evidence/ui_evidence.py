"""轨A 证据适配器：UI/座舱域失败证据（截图序列 + 步迹 + Logcat 切片）。

纯 UI 失败永不查 CAN 报文 —— 本模块只从座舱域证据里提取
崩溃 / ANR / 渲染异常 / 内存杀进程等应用层签名，供轨A 定位根因。
"""
import json
import re
from pathlib import Path
from typing import Dict, List

# 应用层异常签名（规则可解释、可单测）
SIGNATURE_PATTERNS: Dict[str, re.Pattern] = {
    "fatal_crash": re.compile(r"(?i)FATAL EXCEPTION"),
    "anr": re.compile(r"(?i)ANR in"),
    "render_error": re.compile(r"(?i)(EGL|OpenGL|SurfaceFlinger|HWUI).{0,80}(error|fail|timed)"),
    "oom_kill": re.compile(r"(?i)(lowmemorykiller|OutOfMemoryError)"),
    "activity_restart": re.compile(r"(?i)Process .* has died"),
}


class UIEvidenceParser:
    """解析一次失败 UI 用例留下的证据包（由 step_recorder 落盘 / 故障注入生成）。"""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)

    # ---------------- 步迹 ----------------
    def load_steps(self) -> List[dict]:
        # 兼容两种命名：演示案例用 ui_steps.jsonl，真机 step_recorder 用 steps.jsonl
        for name in ("ui_steps.jsonl", "steps.jsonl"):
            path = self.data_dir / name
            if not path.exists():
                continue
            steps = []
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        steps.append(json.loads(line))
                    except Exception:
                        continue
            if steps:
                return steps
        return []

    def screenshots(self) -> List[str]:
        return sorted(str(p) for p in self.data_dir.glob("step_*.png"))

    def logcat_text(self) -> str:
        path = self.data_dir / "tbox_logcat.txt"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")

    # ---------------- 签名提取 ----------------
    def extract_signatures(self, logcat_text: str) -> Dict[str, List[str]]:
        """从 logcat 切片中提取应用层异常签名（含前后文各 1 行）。"""
        found: Dict[str, List[str]] = {}
        lines = logcat_text.splitlines()
        for i, line in enumerate(lines):
            for name, pat in SIGNATURE_PATTERNS.items():
                if pat.search(line):
                    ctx = " | ".join(lines[max(0, i - 1): min(len(lines), i + 2)])
                    found.setdefault(name, []).append(ctx.strip())
        return found

    def build_context(self, max_steps: int = 30, max_log_lines: int = 300) -> str:
        """组装给 LLM 的轨A 上下文（步迹摘要 + 异常签名 + 相关 logcat）。"""
        parts: List[str] = []
        steps = self.load_steps()
        if steps:
            parts.append("【执行步迹】")
            for s in steps[-max_steps:]:
                parts.append(
                    f"- Step {s.get('step', '?')}: action={s.get('action', '?')} "
                    f"target={s.get('target_id', '-')} thought={s.get('thought', '')[:120]}"
                )
        logcat = self.logcat_text()
        sigs = self.extract_signatures(logcat)
        if sigs:
            parts.append("【应用层异常签名】")
            for name, hits in sigs.items():
                for h in hits[:5]:
                    parts.append(f"- [{name}] {h[:400]}")
        else:
            parts.append("【应用层异常签名】未检出崩溃/ANR/渲染异常（可能为逻辑/断言类缺陷）")
        # 保留最近 N 行 logcat 供深挖
        tail = "\n".join(logcat.splitlines()[-max_log_lines:])
        if tail:
            parts.append("【Logcat 尾段】\n" + tail)
        return "\n".join(parts)

    def summary(self) -> dict:
        logcat = self.logcat_text()
        return {
            "steps": len(self.load_steps()),
            "screenshots": len(self.screenshots()),
            "signatures": self.extract_signatures(logcat),
        }
