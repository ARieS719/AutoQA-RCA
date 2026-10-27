"""VLM 客户端：多模态推理网关。

修复记录（对应 P0/P1 工程化）：
1. 模型名/base_url/超时/重试全部走 config，不再硬编码；
2. 请求失败指数退避重试（车机测试夜间长跑时网络抖动常见）；
3. 结构化输出解析集中一处（JSON 提取 + 边界校验），各方法复用。
"""
import base64
import logging
import os
import re
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class VLMClient:
    def __init__(self, api_key: str, base_url: Optional[str] = None,
                 model_name: Optional[str] = None,
                 timeout: float = 60.0, retries: int = 3):
        from openai import OpenAI
        if not api_key:
            raise ValueError("VLMClient 需要 API Key（请配置 SILICONFLOW_API_KEY）")
        self.client = OpenAI(api_key=api_key, base_url=base_url or "https://api.siliconflow.cn/v1")
        self.model_name = model_name or "Qwen/Qwen3-VL-32B-Instruct"
        self.timeout = timeout
        self.retries = retries
        logger.info("VLM 客户端初始化: %s", self.model_name)

    @staticmethod
    def _encode_image(image_path: str) -> str:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def analyze_screen(self, image_path: str, prompt: str) -> str:
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"找不到图片: {image_path}")
        b64 = self._encode_image(image_path)
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }]
        last_err: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model_name, messages=messages,
                    max_tokens=1024, temperature=0.1, timeout=self.timeout)
                return resp.choices[0].message.content or ""
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < self.retries:
                    # 限流/服务繁忙（如 50507）需要比普通超时更长的退避
                    wait = min(10 * (2 ** attempt), 60)
                    logger.warning("VLM 请求失败（第 %d 次），%ds 后重试: %s",
                                   attempt + 1, wait, str(e)[:120])
                    time.sleep(wait)
        raise RuntimeError(f"VLM 请求失败（重试 {self.retries} 次）: {str(last_err)[:150]}")

    # ---------------- 结构化输出解析 ----------------
    @staticmethod
    def extract_json(raw: str) -> dict:
        if not raw:
            return {}
        try:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                import json
                return json.loads(m.group(0))
        except Exception:
            pass
        return {}

    def find_element_coordinates(self, image_path: str, target_name: str,
                                 width: int = 1408, height: int = 792) -> Tuple[int, int]:
        prompt = (
            f"你是精准的UI自动化定位引擎。当前屏幕分辨率：宽 {width}，高 {height}。\n"
            f"任务：在截图中寻找【{target_name}】的中心点像素坐标。\n"
            f"严格输出一个坐标数组 [X, Y]，X 不得超过 {width}，Y 不得超过 {height}，"
            f"不要输出任何其他文字。示例：[550, 750]"
        )
        raw = self.analyze_screen(image_path, prompt)
        numbers = re.findall(r"\d+", raw)
        if len(numbers) >= 2:
            x, y = int(numbers[0]), int(numbers[1])
            x = max(0, min(x, width - 10))
            y = max(0, min(y, height - 10))
            return x, y
        logger.error("坐标解析失败，AI 回复: %s", raw)
        return -1, -1

    def find_element_by_som_id(self, som_image_path: str, target_name: str) -> str:
        prompt = (
            "你是精准的UI自动化定位引擎。观察这张【带有红色数字标签】的截图。\n"
            f"任务：我需要点击【{target_name}】，请只回答它上面标注的红色数字。\n"
            "严格输出一个纯数字，不要任何标点或解释。示例：5"
        )
        raw = self.analyze_screen(som_image_path, prompt)
        numbers = re.findall(r"\d+", raw)
        return str(numbers[0]) if numbers else "-1"

    def assert_screen_state(self, image_path: str, expected_state: str) -> bool:
        prompt = (
            "你是严谨的自动化测试断言引擎。观察截图，判断当前界面是否符合预期："
            f"【{expected_state}】。\n"
            "符合仅输出 TRUE，不符合仅输出 FALSE，不要任何其他字符。"
        )
        raw = self.analyze_screen(image_path, prompt)
        result = raw.strip().upper()
        if "TRUE" in result:
            return True
        if "FALSE" in result:
            return False
        logger.warning("视觉断言解析异常: %s", raw)
        return False
