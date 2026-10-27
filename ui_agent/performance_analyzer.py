"""E2E 性能分析引擎：录屏帧差法量化冷启动/转场耗时。

修复记录（对应 P2-4）：
1. 相似度统一为巴氏距离（README 声称与实现一致）；
2. 拆出 analyze_frame_sequence 纯函数 —— 不依赖视频文件即可离线单测；
3. 状态机逻辑不变：画面开始变化 -> 连续 N 帧稳定判定渲染完成。
"""
import logging
from typing import List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class PerformanceAnalyzer:
    def __init__(self, similarity_threshold: float = 0.01,
                 required_stable_frames: int = 5):
        # 巴氏距离阈值：两帧距离 < 0.01 视为完全静止（渲染结束）
        self.similarity_threshold = similarity_threshold
        self.required_stable_frames = required_stable_frames

    @staticmethod
    def frame_similarity(img1: np.ndarray, img2: np.ndarray) -> float:
        """巴氏距离相似度：1.0 表示完全相同，0.0 表示完全不同。

        兜底：近均匀直方图上巴氏距离可能出现 nan/越界（浮点边界），
        此时退化为归一化直方图平均绝对差，保证返回值恒为 [0,1] 内的有限值。
        """
        gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
        gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
        hist1 = cv2.calcHist([gray1], [0], None, [256], [0, 256])
        hist2 = cv2.calcHist([gray2], [0], None, [256], [0, 256])
        cv2.normalize(hist1, hist1, 0, 1, cv2.NORM_MINMAX)
        cv2.normalize(hist2, hist2, 0, 1, cv2.NORM_MINMAX)
        dist = cv2.compareHist(hist1, hist2, cv2.HISTCMP_BHATTACHARYYA)
        if not np.isfinite(dist) or dist < 0:
            dist = float(np.mean(np.abs(hist1 - hist2)))
        dist = float(min(max(dist, 0.0), 1.0))
        return round(float(1.0 - dist), 6)

    def analyze_frame_sequence(self, frames: List[np.ndarray], fps: float) -> dict:
        """纯函数：对帧序列做转场耗时分析（离线可测）。"""
        result = {"detected": False, "start_frame": -1, "stable_frame": -1,
                  "frames_elapsed": -1, "transition_seconds": -1.0}
        start_change, stable_frame = -1, -1
        stable_count = 0
        for i in range(1, len(frames)):
            sim = self.frame_similarity(frames[i - 1], frames[i])
            if start_change == -1 and sim < 1.0 - self.similarity_threshold:
                start_change = i
            if start_change != -1 and stable_frame == -1:
                if sim >= 1.0 - self.similarity_threshold:
                    stable_count += 1
                    if stable_count >= self.required_stable_frames:
                        stable_frame = i - self.required_stable_frames
                        break
                else:
                    stable_count = 0
        if start_change != -1 and stable_frame != -1:
            frames_elapsed = stable_frame - start_change
            result.update({
                "detected": True,
                "start_frame": start_change,
                "stable_frame": stable_frame,
                "frames_elapsed": frames_elapsed,
                "transition_seconds": round(frames_elapsed / fps, 3) if fps > 0 else -1.0,
            })
        return result

    def analyze_transition_time(self, video_path: str, fps_fallback: int = 30) -> float:
        """文件级入口：读取视频帧序列并返回转场耗时（秒）；-1 表示未检出。"""
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.error("无法打开视频文件: %s", video_path)
            return -1.0
        actual_fps = cap.get(cv2.CAP_PROP_FPS)
        if actual_fps <= 0 or actual_fps > 120:
            actual_fps = fps_fallback
        frames: List[np.ndarray] = []
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frames.append(frame)
        cap.release()
        logger.info("视频实际帧率: %.2f FPS，共 %d 帧", actual_fps, len(frames))
        res = self.analyze_frame_sequence(frames, actual_fps)
        if res["detected"]:
            logger.info("转场动画耗时: %.3f 秒（历经 %d 帧，分辨率 %.1fms）",
                        res["transition_seconds"], res["frames_elapsed"], 1000.0 / actual_fps)
        else:
            logger.warning("未检测到完整转场生命周期（画面一直静止或动画未结束）")
        return res["transition_seconds"]
