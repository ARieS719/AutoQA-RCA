"""Token / 成本计量：把"AI 提效"落成可量化的数字。

说明（诚实口径）：
- Token 数为**估算**（不引入 tiktoken 等分词器）：中文按 ~0.65 token/字、
  英文按 ~3.5 字符/token 的经验系数估算，误差通常在 ±15% 内；
- 单价为**可配置**的公开价目（默认按 DeepSeek-V3 输入 ¥2/M、输出 ¥8/M），
  最终费用以厂商账单为准；
- 降噪节省 = 被剔除噪音行数 × 实际保留行的平均估算 token（输入侧节省）。
"""
import re
from dataclasses import dataclass, field
from typing import Dict, Optional

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


class TokenEstimator:
    @staticmethod
    def estimate(text: str) -> int:
        if not text:
            return 0
        cjk = len(_CJK_RE.findall(text))
        other = max(len(text) - cjk, 0)
        return int(cjk * 0.65 + other / 3.5)


@dataclass
class CostTracker:
    """跨节点累计 LLM 用量与费用估算。"""

    input_price_per_1m: float = 2.0    # 元 / 百万输入 token
    output_price_per_1m: float = 8.0   # 元 / 百万输出 token
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    by_node: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def record(self, node: str, prompt_text: str, completion_text: str = "") -> None:
        p = TokenEstimator.estimate(prompt_text)
        c = TokenEstimator.estimate(completion_text)
        self.prompt_tokens += p
        self.completion_tokens += c
        self.calls += 1
        entry = self.by_node.setdefault(node, {"prompt": 0, "completion": 0, "calls": 0})
        entry["prompt"] += p
        entry["completion"] += c
        entry["calls"] += 1

    def est_cost_cny(self) -> float:
        return (self.prompt_tokens / 1_000_000 * self.input_price_per_1m
                + self.completion_tokens / 1_000_000 * self.output_price_per_1m)

    def noise_savings(self, dropped_noise: int, avg_tokens_per_line: float) -> dict:
        saved = int(dropped_noise * avg_tokens_per_line)
        return {
            "noise_lines_filtered": dropped_noise,
            "avg_tokens_per_line": round(avg_tokens_per_line, 1),
            "est_input_tokens_saved": saved,
            "est_input_cost_saved_cny": round(
                saved / 1_000_000 * self.input_price_per_1m, 4),
        }

    def summary(self, savings: Optional[dict] = None) -> dict:
        return {
            "llm_calls": self.calls,
            "est_prompt_tokens": self.prompt_tokens,
            "est_completion_tokens": self.completion_tokens,
            "est_cost_cny": round(self.est_cost_cny(), 4),
            "unit_prices_cny_per_1m": {
                "input": self.input_price_per_1m,
                "output": self.output_price_per_1m,
            },
            "by_node": dict(self.by_node),
            "noise_savings": savings or {},
            "note": "Token 为启发式估算（中文≈0.65 token/字，英文≈3.5 字符/token），费用以厂商账单为准",
        }
