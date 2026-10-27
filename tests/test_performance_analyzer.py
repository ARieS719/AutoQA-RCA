"""帧差性能分析离线单测：转场状态机（变化 -> 连续稳定判定）。"""
import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from ui_agent.performance_analyzer import PerformanceAnalyzer


def _solid(color: int, shape=(100, 100, 3)) -> np.ndarray:
    img = np.zeros(shape, dtype=np.uint8)
    img[:] = color
    return img


def test_transition_detection():
    analyzer = PerformanceAnalyzer(required_stable_frames=5)
    frames = (
        [_solid(20)] * 10 +      # 静止画面 A
        [_solid(80), _solid(140), _solid(200)] +  # 转场中间帧
        [_solid(240)] * 15       # 静止画面 B
    )
    res = analyzer.analyze_frame_sequence(frames, fps=30)
    assert res["detected"] is True
    # 变化从第 10 帧开始，稳定判定于第 13 帧 => 3 帧转场
    assert res["start_frame"] == 10
    assert res["frames_elapsed"] == 3
    assert res["transition_seconds"] == pytest.approx(0.1)


def test_no_transition_when_static():
    analyzer = PerformanceAnalyzer()
    frames = [_solid(120)] * 30
    res = analyzer.analyze_frame_sequence(frames, fps=30)
    assert res["detected"] is False
    assert res["transition_seconds"] == -1.0


def test_similarity_scale():
    # 相同图 -> 相似度 1；黑白图 -> 相似度接近 0
    assert PerformanceAnalyzer.frame_similarity(_solid(10), _solid(10)) == pytest.approx(1.0)
    assert PerformanceAnalyzer.frame_similarity(_solid(10), _solid(250)) < 0.05


def test_similarity_always_finite_on_noise():
    """回归：近均匀直方图下巴氏距离可能 nan/越界，必须兜底为有限值。"""
    rng = np.random.default_rng(42)
    for _ in range(10):
        a = rng.integers(0, 256, (100, 100, 3), dtype=np.uint8)
        b = rng.integers(0, 256, (100, 100, 3), dtype=np.uint8)
        sim = PerformanceAnalyzer.frame_similarity(a, b)
        assert np.isfinite(sim) and 0.0 <= sim <= 1.0
