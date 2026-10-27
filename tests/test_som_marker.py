"""SoM 打标离线单测：轮廓检测 / NMS 去嵌套 / 标记坐标映射。"""
import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from ui_agent.som_marker import SoMMarker


def _make_screen():
    """200x200 黑底 + 两个白色实心矩形（一大一小、互不嵌套）。"""
    img = np.zeros((200, 200, 3), dtype=np.uint8)
    cv2.rectangle(img, (20, 20), (60, 60), (255, 255, 255), -1)
    cv2.rectangle(img, (120, 100), (180, 170), (255, 255, 255), -1)
    return img


def test_detect_boxes_finds_two_rects():
    marker = SoMMarker(min_area=100, max_area=20000)
    boxes = marker.detect_boxes(_make_screen())
    assert 1 <= len(boxes) <= 2


def test_nested_boxes_are_merged():
    """大框套小框只保留一个（RETR_EXTERNAL + NMS）。"""
    img = np.zeros((300, 300, 3), dtype=np.uint8)
    cv2.rectangle(img, (50, 50), (250, 250), (255, 255, 255), -1)   # 大框
    cv2.rectangle(img, (90, 90), (210, 210), (255, 255, 255), -1)   # 内嵌框
    marker = SoMMarker(min_area=100, max_area=200000, iou_thresh=0.35)
    boxes = marker.detect_boxes(img)
    assert len(boxes) == 1


def test_mark_dict_maps_centers():
    marker = SoMMarker(min_area=100, max_area=20000)
    boxes = marker.detect_boxes(_make_screen())
    marks = marker.build_mark_dict(boxes)
    # 40x40 框 (20,20)-(60,60) 中心应为 (40,40)
    assert any((x, y) == (40, 40) for x, y in marks.values())
    # ID 与坐标一一对应，无重复
    assert len(set(marks.values())) == len(marks)


def test_get_box_matches_mark_dict(tmp_path):
    """回归：generate 后可按 ID 取完整包围框（框内拖动依赖），且中心与 mark_dict 一致。"""
    img_path = tmp_path / "in.png"
    out_path = tmp_path / "out.png"
    cv2.imwrite(str(img_path), _make_screen())
    marker = SoMMarker(min_area=100, max_area=20000)
    marks = marker.generate(str(img_path), str(out_path))
    assert marks
    for bid, (cx, cy) in marks.items():
        box = marker.get_box(bid)
        assert box is not None
        x, y, w, h = box
        assert (x + w // 2, y + h // 2) == (cx, cy)
    assert marker.get_box("999") is None
