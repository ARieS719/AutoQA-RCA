"""ground_truth（答案卡）的机器可读 schema：评测基准的依据。

evaluate.py 用本模块读写真相卡，实现"AI 判定的根因/责任/轨道/仲裁
与真相自动比对"。人工可读版仍为 ground_truth.md。
"""
import json
from pathlib import Path
from typing import Dict, Optional

TRUTH_FILE = "ground_truth.json"


def build_truth(expected_track: dict, root_cause_keywords: list,
                responsibility_keywords: list, rag_hit: str = "",
                arbitration_direction: str = "single",
                note: str = "") -> dict:
    """构造标准真相卡结构。

    expected_track: {"ui": bool, "telematics": bool}
    arbitration_direction: single | telematics | telematics->ui | manual
    """
    return {
        "expected_track": dict(expected_track),
        "arbitration_direction": arbitration_direction,
        "root_cause_keywords": list(root_cause_keywords),
        "responsibility_keywords": list(responsibility_keywords),
        "rag_hit": rag_hit,
        "note": note,
    }


def write_truth(case_dir, truth: dict) -> Path:
    case_dir = Path(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)
    path = case_dir / TRUTH_FILE
    path.write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_truth(case_dir) -> Optional[dict]:
    path = Path(case_dir) / TRUTH_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
