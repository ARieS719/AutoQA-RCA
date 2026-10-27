"""SoM（Set-of-Mark）视觉打标引擎。

修复记录（对应 P2-1）：
1. 仅取外层轮廓（RETR_EXTERNAL），消除"卡片套按钮"的嵌套重复标记；
2. 重叠框按面积优先 + IoU 合并（NMS），复杂界面不再"标记爆炸"；
3. 数字标签做边界夹取，屏幕顶部元素不再画出界；
4. 标记坐标映射表与画图解耦 —— 纯函数可离线单测。
"""
import logging
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def _iou(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    """两框 IoU（a/b 均为 x, y, w, h）。"""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _nms_merge(boxes: List[Tuple[int, int, int, int]], iou_thresh: float = 0.35):
    """按面积降序保留、重叠即弃（去嵌套 + 去重）。"""
    boxes = sorted(boxes, key=lambda b: b[2] * b[3], reverse=True)
    keep: List[Tuple[int, int, int, int]] = []
    for b in boxes:
        if all(_iou(b, k) < iou_thresh for k in keep):
            keep.append(b)
    return keep


class SoMMarker:
    def __init__(self, min_area: int = 500, max_area: int = 50000,
                 canny_low: int = 50, canny_high: int = 150,
                 iou_thresh: float = 0.35):
        self.min_area = min_area
        self.max_area = max_area
        self.canny_low = canny_low
        self.canny_high = canny_high
        self.iou_thresh = iou_thresh
        self._boxes_by_id: Dict[str, Tuple[int, int, int, int]] = {}

    def get_box(self, box_id: str) -> Optional[Tuple[int, int, int, int]]:
        """按标记 ID 取完整包围框 (x, y, w, h)——支持框内拖动等精细动作。"""
        return self._boxes_by_id.get(str(box_id))

    # ---------------- 纯函数核心（可离线单测） ----------------
    def detect_boxes(self, img: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """边缘检测 + 轮廓提取 + NMS，返回 (x, y, w, h) 列表。"""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, self.canny_low, self.canny_high)
        kernel = np.ones((3, 3), np.uint8)
        dilated = cv2.dilate(edges, kernel, iterations=2)
        # 只取外层轮廓，天然排除嵌套子元素
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for c in contours:
            area = cv2.contourArea(c)
            if self.min_area < area < self.max_area:
                boxes.append(tuple(map(int, cv2.boundingRect(c))))
        return _nms_merge(boxes, self.iou_thresh)

    def build_mark_dict(self, boxes: List[Tuple[int, int, int, int]]) -> Dict[str, Tuple[int, int]]:
        """给每个框分配稳定 ID（按位置排序，保证同屏重打标时 ID 稳定）。"""
        mark: Dict[str, Tuple[int, int]] = {}
        for i, (x, y, w, h) in enumerate(sorted(boxes, key=lambda b: (b[1], b[0])), start=1):
            mark[str(i)] = (x + w // 2, y + h // 2)
        return mark

    def draw_marks(self, img: np.ndarray, boxes: List[Tuple[int, int, int, int]]) -> np.ndarray:
        """在图上绘制绿框 + 红底数字标签（含边界夹取）。"""
        out = img.copy()
        h_img = out.shape[0]
        for i, (x, y, w, h) in enumerate(sorted(boxes, key=lambda b: (b[1], b[0])), start=1):
            cv2.rectangle(out, (x, y), (x + w, y + h), (0, 255, 0), 2)
            # 标签框：默认画在框上方，越界则改画框内左上角
            if y - 20 >= 0:
                lx, ly = x, y - 20
            else:
                lx, ly = x, y
            ly = min(ly, max(0, h_img - 22))
            cv2.rectangle(out, (lx, ly), (lx + 30, ly + 20), (0, 0, 255), -1)
            cv2.putText(out, str(i), (lx + 5, ly + 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        return out

    # ---------------- 文件级接口（与旧 API 兼容） ----------------
    def generate(self, input_path: str, output_path: str) -> Dict[str, Tuple[int, int]]:
        img = cv2.imread(input_path)
        if img is None:
            logger.error("无法读取图片: %s", input_path)
            return {}
        boxes = self.detect_boxes(img)
        mark_dict = self.build_mark_dict(boxes)
        # 固化 ID -> 完整包围框映射（与 mark_dict 的排序规则一致）
        ordered = sorted(boxes, key=lambda b: (b[1], b[0]))
        self._boxes_by_id = {str(i + 1): box for i, box in enumerate(ordered)}
        cv2.imwrite(output_path, self.draw_marks(img, boxes))
        logger.info("SoM 标记图已生成，共识别 %d 个潜在控件 -> %s", len(boxes), output_path)
        return mark_dict

    # 兼容旧调用名
    generate_som_image = generate
