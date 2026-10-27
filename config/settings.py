# ============== 统一配置中心 ==============
# 所有魔法值集中于此，模块禁止各自散落硬编码。
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple

try:
    from dotenv import load_dotenv
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:  # dotenv 未安装时降级为纯环境变量
    PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


@dataclass
class Settings:
    # ---- 模型网关（硅基流动，OpenAI 兼容协议）----
    api_key: str = ""
    base_url: str = "https://api.siliconflow.cn/v1"
    llm_model: str = "deepseek-ai/DeepSeek-V3"        # 诊断/文本推理模型
    vlm_model: str = "Qwen/Qwen3-VL-32B-Instruct"     # 视觉执行与断言模型
    embedding_model: str = "BAAI/bge-m3"              # RAG 向量化模型
    vlm_timeout: float = 60.0
    vlm_retries: int = 3

    # ---- 成本计量（估算口径，单位：元 / 百万 token；默认 DeepSeek-V3 公开价目）----
    llm_input_price_per_1m: float = 2.0
    llm_output_price_per_1m: float = 8.0

    # ---- 路径 ----
    data_dir: Path = PROJECT_ROOT / "data_output"
    reports_dir: Path = PROJECT_ROOT / "reports"
    knowledge_file: Path = PROJECT_ROOT / "knowledge_base" / "history_bugs.txt"
    dbc_file: Path = PROJECT_ROOT / "knowledge_base" / "signal.dbc"
    rag_persist_dir: Path = PROJECT_ROOT / "data_output" / "chroma_db"

    # ---- 确定性数据管道 ----
    ignore_keywords: Tuple[str, ...] = (
        "0x1A4", "0x2B5", "WifiMonitor", "ActivityManager", "Battery level normal",
    )

    # ---- 证据门签名（正则可解释：命中即认为失败可能牵连车联网链路）----
    # 注意：不能用过宽的 r"network"（WifiMonitor 的 "Scanning for networks" 是常规噪音，
    # 会误开轨B），需收窄到"网络异常动作"层面。
    evidence_gate_patterns: Tuple[str, ...] = (
        r"(?i)\bMQTT\b", r"(?i)\bsocket\b", r"(?i)\bCanHal\b",
        r"(?i)T-?BOX", r"(?i)timeout", r"(?i)Ping\s+Timeout",
        r"(?i)network\s+(manager|error|unavailable|disconnect)",
        r"(?i)连接", r"(?i)超时", r"(?i)信号",
    )

    # ---- UI Agent ----
    max_steps: int = 10
    max_run_seconds: int = 1200          # 单任务超时熔断（秒），批量长跑的止损阀
    screen_width: int = 1408
    screen_height: int = 792
    action_pause_seconds: float = 2.0
    repeat_action_limit: int = 3                 # 同一动作重复上限，防死循环

    @classmethod
    def load(cls) -> "Settings":
        def _f(name, default):
            try:
                return float(_env(name, str(default)))
            except Exception:
                return default

        def _i(name, default):
            try:
                return int(_env(name, str(default)))
            except Exception:
                return default

        keywords = tuple(
            k.strip() for k in _env("IGNORE_KEYWORDS", ",".join(cls().ignore_keywords)).split(",") if k.strip()
        )
        return cls(
            api_key=_env("SILICONFLOW_API_KEY", ""),
            base_url=_env("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"),
            llm_model=_env("LLM_MODEL", "deepseek-ai/DeepSeek-V3"),
            vlm_model=_env("VLM_MODEL", "Qwen/Qwen3-VL-32B-Instruct"),
            embedding_model=_env("EMBEDDING_MODEL", "BAAI/bge-m3"),
            vlm_timeout=_f("VLM_TIMEOUT", 60.0),
            vlm_retries=_i("VLM_RETRIES", 3),
            llm_input_price_per_1m=_f("LLM_INPUT_PRICE_PER_1M", 2.0),
            llm_output_price_per_1m=_f("LLM_OUTPUT_PRICE_PER_1M", 8.0),
            data_dir=Path(_env("DATA_DIR", str(PROJECT_ROOT / "data_output"))),
            reports_dir=Path(_env("REPORTS_DIR", str(PROJECT_ROOT / "reports"))),
            knowledge_file=Path(_env("KNOWLEDGE_FILE", str(PROJECT_ROOT / "knowledge_base" / "history_bugs.txt"))),
            dbc_file=Path(_env("DBC_FILE", str(PROJECT_ROOT / "knowledge_base" / "signal.dbc"))),
            rag_persist_dir=Path(_env("RAG_PERSIST_DIR", str(PROJECT_ROOT / "data_output" / "chroma_db"))),
            ignore_keywords=keywords,
            max_steps=_i("MAX_STEPS", 10),
            max_run_seconds=_i("MAX_RUN_SECONDS", 1200),
            screen_width=_i("SCREEN_WIDTH", 1408),
            screen_height=_i("SCREEN_HEIGHT", 792),
            action_pause_seconds=_f("ACTION_PAUSE_SECONDS", 2.0),
            repeat_action_limit=_i("REPEAT_ACTION_LIMIT", 3),
        )


settings = Settings.load()
