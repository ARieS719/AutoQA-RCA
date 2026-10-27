"""ReAct 自主执行引擎（视觉锚点 + 潜意识自愈 + 步骤证据）。

修复记录（对应 P2-2）：
1. 历史记忆：把最近 N 步动作摘要注入 Prompt，Agent 不再"金鱼记忆"；
2. 动作去重：同一 (动作,目标) 连续重复达到上限即强制换策略/熔断，
   防死循环不再只靠 max_steps 兜底；
3. anomaly_detected 真正参与决策：命中即记录"干预事件"入证据包，
   且该步不计入任务推进步数（弹窗清除不消耗主线预算）；
4. 全程对接 StepRecorder —— 失败即产出 incident_bridge 可直接消费的证据包；
5. 返回值从 bool 升级为结构化结果（success/steps/run_dir）。
"""
import logging
import os
import shutil
import time
from typing import Dict, List, Optional

import cv2

logger = logging.getLogger(__name__)


class AgentEngine:
    def __init__(self, adb_controller, vlm_client, som_marker,
                 recorder=None, settings=None, max_steps: Optional[int] = None,
                 max_run_seconds: Optional[int] = None):
        self.adb = adb_controller
        self.vlm = vlm_client
        self.marker = som_marker
        self.recorder = recorder
        # 避免 import 循环：settings 由外部注入（config.settings.Settings）
        # max_steps / max_run_seconds 可被批量 runner 覆盖（止损阀）
        self.max_steps = max_steps if max_steps else getattr(settings, "max_steps", 10)
        self.max_run_seconds = (max_run_seconds if max_run_seconds
                                else getattr(settings, "max_run_seconds", 1200))
        self.screen_width = getattr(settings, "screen_width", 1408)
        self.screen_height = getattr(settings, "screen_height", 792)
        self.action_pause = getattr(settings, "action_pause_seconds", 2.0)
        self.repeat_limit = getattr(settings, "repeat_action_limit", 3)
        self.raw_path = "temp_engine_raw.png"
        self.som_path = "temp_engine_som.png"

    # ---------------- 核心循环 ----------------
    def execute_task(self, goal: str, case_id: str = "ui_task",
                     domain: str = "ui", link_dependency: Optional[List[str]] = None,
                     symptom: str = "", work_dir: Optional[str] = None,
                     expected_state: Optional[str] = None) -> dict:
        """执行宏观自然语言目标，返回结构化结果（失败即含证据包路径）。

        work_dir: 临时截图落盘目录；默认写入 step_recorder 的运行目录（证据包内），
                  保证截图与步迹同包，供 incident_bridge 直接消费。
        expected_state: 可选独立验收断言（界面状态描述）。AI 报 DONE 后不直接采信，
                  而是再截一张图用独立断言 prompt 判 TRUE/FALSE —— 自报 + 验证双闸门。
        """
        if self.recorder is not None:
            self.recorder.start(title=goal, domain=domain,
                                link_dependency=link_dependency, symptom=symptom)
        if not work_dir and self.recorder is not None and self.recorder.run_dir is not None:
            work_dir = str(self.recorder.run_dir)
        if work_dir:
            os.makedirs(work_dir, exist_ok=True)
            self.raw_path = os.path.join(work_dir, "temp_engine_raw.png")
            self.som_path = os.path.join(work_dir, "temp_engine_som.png")
        else:
            self.raw_path = "temp_engine_raw.png"
            self.som_path = "temp_engine_som.png"
        self.after_path = (os.path.join(work_dir, "after_step.png")
                           if work_dir else "after_step.png")

        history: List[str] = []
        repeat_counter: Dict[str, int] = {}
        intervention_count = 0
        steps_taken = 0
        forced_changes = 0
        noop_feedback = ""
        swipe_noop_streak = 0

        logger.info("===== 启动全自主 Agent 任务: [%s] =====", goal)
        task_t0 = time.time()
        for step in range(1, self.max_steps + 1):
            # 止损阀：单任务超时熔断（批量长跑时兜底，防止单个任务无限拖时间）
            if time.time() - task_t0 > self.max_run_seconds:
                return self._finish(False, f"单任务超时熔断（>{self.max_run_seconds}s）", self.recorder)
            logger.info("--- 第 [%d/%d] 步 ---", step, self.max_steps)
            if not self.adb.get_screenshot(self.raw_path):
                return self._finish(False, "无法获取屏幕状态", self.recorder)
            mark_dict = self.marker.generate(self.raw_path, self.som_path)
            if not mark_dict:
                logger.warning("当前屏幕未识别到任何控件")
                continue

            prompt = self._build_prompt(goal, mark_dict, history, noop_feedback,
                                        expected_state)
            raw = self.vlm.analyze_screen(self.som_path, prompt)
            decision = self.vlm.extract_json(raw)
            if not decision:
                logger.error("决策解析异常: %s", raw)
                continue

            has_anomaly = bool(decision.get("anomaly_detected", False))
            thought = str(decision.get("thought", ""))[:300]
            action = str(decision.get("action", "")).upper()
            target_id = str(decision.get("target_id", ""))

            if self.recorder is not None:
                # 每步打标截图落盘证据包（临时图下一步会被覆盖，先拷贝固化）
                step_png = self.som_path
                if work_dir:
                    try:
                        step_png = os.path.join(work_dir, f"step_{step}_som.png")
                        shutil.copy(self.som_path, step_png)
                    except Exception:  # noqa: BLE001
                        step_png = self.som_path
                self.recorder.record_step(step, action, target_id, thought,
                                          anomaly=has_anomaly,
                                          screenshot_path=step_png)

            # 潜意识自愈：干预事件记录但不消耗主线推进预算
            if has_anomaly:
                intervention_count += 1
                logger.warning("[警报] 检测到干扰物，触发自愈（本任务累计 %d 次）", intervention_count)
            logger.info("思考: %s | 动作: %s %s", thought, action, f"目标 {target_id}" if target_id else "")

            if action in ("DONE", "FAIL"):
                if action == "DONE" and expected_state:
                    # 独立验收闸门：不采信 AI 自报，另行断言最终界面状态
                    assert_path = (os.path.join(work_dir, "final_assert.png")
                                   if work_dir else "final_assert.png")
                    assert_ok = False
                    try:
                        self.adb.get_screenshot(assert_path)
                        assert_ok = self.vlm.assert_screen_state(assert_path, expected_state)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("独立断言执行异常: %s", e)
                    if self.recorder is not None:
                        self.recorder.record_step(step, "ASSERT", "",
                                                  f"expected_state 独立断言: {assert_ok}",
                                                  anomaly=False, screenshot_path=assert_path)
                    if assert_ok:
                        return self._finish(True, "AI 判定完成 + 独立断言通过", self.recorder)
                    logger.warning("AI 判定 DONE，但独立断言未通过：%s", expected_state)
                    return self._finish(False, "AI 判定完成但独立断言失败", self.recorder)
                note = "AI 判定任务闭环" if action == "DONE" else "AI 判定任务无法完成"
                return self._finish(action == "DONE", note, self.recorder)

            if action in ("TAP", "SWIPE_DOWN", "SWIPE_UP", "SWIPE_RIGHT", "SWIPE_LEFT") \
                    and target_id not in mark_dict:
                logger.error("无效 ID [%s]", target_id)
                continue

            # 动作去重：连续重复同一动作即熔断换策略
            key = f"{action}:{target_id or 'NA'}"
            repeat_counter[key] = repeat_counter.get(key, 0) + 1
            if repeat_counter[key] > self.repeat_limit:
                forced_changes += 1
                if forced_changes >= 2:
                    return self._finish(False, "连续换策略仍无进展，主动熔断（防浪费步数）", self.recorder)
                logger.warning("动作 %s 重复 %d 次，判定陷入死循环，强制换策略（改为整屏滑动）",
                               key, repeat_counter[key])
                repeat_counter.clear()
                self.adb.swipe(self.screen_width // 2, int(self.screen_height * 0.75),
                               self.screen_width // 2, int(self.screen_height * 0.25), duration=600)
                noop_feedback = ("⚠ 检测到你在重复无效动作，已替你整屏滑动换页。"
                                 "请改用完全不同的策略，不要再点击同一目标。")
                time.sleep(self.action_pause)
                continue

            if action == "BACK":
                self.adb.press_back()
            elif action == "HOME":
                self.adb.press_home()
            elif action in ("SWIPE_RIGHT", "SWIPE_LEFT"):
                # 框内水平拖动：针对滑块/开关类控件（中心点击对滑块=设到中间值，必须拖动）
                box = self.marker.get_box(target_id) if hasattr(self.marker, "get_box") else None
                if box is None:
                    logger.error("无法获取控件框 [%s]，跳过拖动", target_id)
                    continue
                bx, by, bw, bh = box
                cy = by + bh // 2
                # 拖动范围放宽到 4%~96%：Android 滑块拇指停在手势终点，
                # 15%~85% 会让滑块停在 85% 处而非最右端
                x1 = bx + int(bw * 0.04)
                x2 = bx + int(bw * 0.96)
                if action == "SWIPE_LEFT":
                    x1, x2 = x2, x1
                logger.info("框内水平拖动 ID=%s: (%d,%d)->(%d,%d)", target_id, x1, cy, x2, cy)
                self.adb.swipe(x1, cy, x2, cy, duration=600)
            elif action == "TAP":
                x, y = mark_dict[target_id]
                self.adb.tap(x, y)
            elif action in ("SWIPE_DOWN", "SWIPE_UP"):
                anchor_x, _ = mark_dict[target_id]
                y_start = int(self.screen_height * 0.75) if action == "SWIPE_DOWN" else int(self.screen_height * 0.25)
                y_end = int(self.screen_height * 0.25) if action == "SWIPE_DOWN" else int(self.screen_height * 0.75)
                logger.info("锚点滑动：锁定 X=%d，%s", anchor_x, action)
                self.adb.swipe(anchor_x, y_start, anchor_x, y_end, duration=800)
            else:
                logger.error("不支持的动作: %s", action)
                continue

            steps_taken += 1
            time.sleep(self.action_pause)

            # ---- no-op 检测：动作后画面无变化 -> 标记无效并注入反馈，加速换策略 ----
            effect = "unknown"
            if self.adb.get_screenshot(self.after_path):
                effect = self._detect_effect()
            noop_feedback = ""
            if effect == "no-op":
                repeat_counter[key] = repeat_counter.get(key, 0) + 1
                noop_feedback = (
                    f"⚠ 你上一步 {action} target={target_id} 执行后界面没有任何变化（判定为无效操作）。"
                    f"请不要再重复它，改用完全不同的策略。")
                if action in ("SWIPE_DOWN", "SWIPE_UP", "SWIPE_RIGHT", "SWIPE_LEFT"):
                    swipe_noop_streak += 1
                    if swipe_noop_streak >= 3:
                        return self._finish(False, "连续滑动无效，页面无可滚动内容，主动熔断", self.recorder)
                    noop_feedback = ("⚠ 滑动无效：页面可能没有可滚动内容，或已经滑到滚动边界。"
                                     "请停止滑动，改用其他方式（点击可见选项、BACK 返回、或输出 FAIL）。")
            else:
                if action in ("SWIPE_DOWN", "SWIPE_UP", "SWIPE_RIGHT", "SWIPE_LEFT"):
                    swipe_noop_streak = 0
            history.append(f"Step{step}: {action} target={target_id} (干扰={has_anomaly}, 效果={effect})")
            history = history[-6:]          # 最近 6 步记忆

        return self._finish(False, f"触达最大步数 {self.max_steps}，熔断", self.recorder)

    # ---------------- 内部 ----------------
    def _build_prompt(self, goal: str, mark_dict: dict, history: List[str],
                      noop_feedback: str = "", expected_state: str = "") -> str:
        history_text = "\n".join(history[-6:]) if history else "（尚无历史动作）"
        feedback_text = noop_feedback or "（上一步未检测到无效操作）"
        criteria = expected_state or "见任务目标描述"
        return f"""
你是一个完全自主的 GUI UI测试 Agent，具备异常自愈能力。
你的终极任务目标：【{goal}】。

观察当前屏幕截图（已附加红色数字标签）。可点击控件 ID 列表：{list(mark_dict.keys())}。

【最近 6 步历史（含每步效果反馈）】
{history_text}

【上一步执行反馈（务必遵守）】
{feedback_text}

【任务完成标准（达到即立即输出 DONE，不要再做多余动作）】
{criteria}
判断要点：以屏幕主内容区为准。屏幕底部的常驻应用栏在应用打开后依然可见——
"底部图标存在"不代表任务未完成。若主内容区已满足完成标准，直接输出 DONE，
禁止再去点击底部图标。

【心智模型与操作优先级】
Priority 1（异常检测与自愈）：先扫描全局屏幕，检查是否有阻挡主界面的模态弹窗
（低电量警告、权限申请 Allow/Deny、应用崩溃提示、系统更新、广告遮挡等）。
注意：非模态的提示横幅/信息卡片（如"连接蓝牙"类提示）不是需要清除的弹窗，
不要为之浪费动作；只有当弹窗带明显关闭/确认按钮且阻挡操作时才需自愈处理。

Priority 2（主线任务）：屏幕干净时，推理如何推进终极任务目标。

【可用动作】
1. TAP: 点击某个具体控件（主线目标或关闭弹窗按钮都用它）。
2. SWIPE_DOWN: 查看下方内容（手指从下往上划）。必须提供滑动区域内一个控件 ID 作为锚点。
3. SWIPE_UP: 查看上方内容（手指从上往下划）。锚点规则同上。
   注意：若页面内容很短、没有滚动条，滑动不会有任何变化——此时不要反复滑动，
   改用点击可见选项、BACK 返回，或输出 FAIL。
4. SWIPE_RIGHT: 在指定控件的框内向右水平拖动（把滑块拖到最大/开关拨到开启）。target_id 必填。
5. SWIPE_LEFT: 在指定控件的框内向左水平拖动（把滑块拖到最小/开关拨到关闭）。target_id 必填。
6. BACK: 返回上一页（Android 返回键）。
7. HOME: 回到主桌面（Home 键）。
8. DONE: 终极目标已完全达成。
9. FAIL: 尝试所有办法仍无法达成，主动放弃。

【严格输出】只返回一个 JSON：
{{"anomaly_detected": true或false, "thought": "思考过程",
  "action": "TAP|SWIPE_DOWN|SWIPE_UP|SWIPE_RIGHT|SWIPE_LEFT|BACK|HOME|DONE|FAIL",
  "target_id": "数字标签（TAP/SWIPE 系列必填，BACK/HOME 可留空）"}}
"""

    def _detect_effect(self) -> str:
        """对比动作前后截图，判定界面是否变化（no-op 检测）。"""
        if not os.path.exists(self.raw_path) or not os.path.exists(self.after_path):
            return "unknown"
        try:
            from ui_agent.performance_analyzer import PerformanceAnalyzer
            img1 = cv2.imread(self.raw_path)
            img2 = cv2.imread(self.after_path)
            if img1 is None or img2 is None:
                return "unknown"
            sim = PerformanceAnalyzer.frame_similarity(img1, img2)
            # 阈值 0.995：同为暗色主题的两屏可能仅差 0.01（如打开设置），0.99 会误判 no-op
            logger.info("动作前后画面相似度: %.4f -> %s", sim,
                        "no-op(界面无变化)" if sim >= 0.995 else "changed(界面已变化)")
            return "no-op" if sim >= 0.995 else "changed"
        except Exception as e:  # noqa: BLE001
            logger.warning("no-op 检测异常: %s", e)
            return "unknown"

    @staticmethod
    def _finish(success: bool, note: str, recorder) -> dict:
        result = {"success": success, "note": note, "run_dir": None, "steps": 0}
        if recorder is not None:
            result["run_dir"] = str(recorder.finish(success, note))
        logger.info("任务结束: success=%s, note=%s", success, note)
        return result
