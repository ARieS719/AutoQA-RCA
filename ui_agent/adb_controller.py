"""ADB 设备控制层。

修复记录（对应 P2-3）：
1. 截图远程路径带时间戳，并发跑两条用例不再互相覆盖；
2. 命令执行增加重试（设备抖动时不再一败即溃）；
3. 录屏守护支持 --time-limit 兜底，并显式报告启动失败原因；
4. 新增 logcat 转储 —— 失败证据包的原料（对接 incident_bridge）。
"""
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class AdbController:
    def __init__(self, adb_bin: str = "adb", retries: int = 2, retry_pause: float = 1.0):
        self.adb_bin = adb_bin
        self.retries = retries
        self.retry_pause = retry_pause
        self.record_process = None
        self._remote_video_path = None
        try:
            self.run_adb_cmd(["version"])
        except Exception as e:
            logger.warning("ADB 初始化失败（若未连接设备属正常）: %s", e)

    def run_adb_cmd(self, args: List[str]) -> str:
        """执行 ADB 命令，带重试；失败抛异常（调用方决定降级策略）。"""
        last_err: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                result = subprocess.run(
                    [self.adb_bin] + args, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", check=True, timeout=60)
                return result.stdout.strip()
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < self.retries:
                    time.sleep(self.retry_pause)
        raise RuntimeError(f"ADB 命令失败 {args}: {last_err}")

    # ---------------- 截图 ----------------
    def get_screenshot(self, save_path) -> bool:
        """采集屏幕截图；远端路径唯一化，避免并发覆盖。"""
        remote = f"/sdcard/temp_screen_{int(time.time() * 1000)}.png"
        try:
            self.run_adb_cmd(["shell", "screencap", "-p", remote])
            self.run_adb_cmd(["pull", remote, str(save_path)])
            self.run_adb_cmd(["shell", "rm", remote])
            logger.debug("截图已保存: %s", save_path)
            return True
        except Exception as e:
            logger.error("截图采集失败: %s", e)
            return False

    # ---------------- 触控 ----------------
    def tap(self, x: int, y: int) -> None:
        logger.info("点击 (%d, %d)", x, y)
        self.run_adb_cmd(["shell", "input", "tap", str(x), str(y)])

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 500) -> None:
        logger.info("滑动 (%d,%d)->(%d,%d) %dms", x1, y1, x2, y2, duration)
        self.run_adb_cmd(["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration)])

    def press_home(self) -> None:
        self.run_adb_cmd(["shell", "input", "keyevent", "KEYCODE_HOME"])

    def press_back(self) -> None:
        self.run_adb_cmd(["shell", "input", "keyevent", "KEYCODE_BACK"])

    def wake_and_unlock(self) -> bool:
        """点亮并解锁屏幕（批量实测前收敛设备状态）。"""
        try:
            self.run_adb_cmd(["shell", "input", "keyevent", "KEYCODE_WAKEUP"])
            self.run_adb_cmd(["shell", "wm", "dismiss-keyguard"])
            time.sleep(1)
            return True
        except Exception as e:
            logger.warning("点亮/解锁失败（可能本已亮屏）: %s", e)
            return False

    # ---------------- 录屏守护（性能基准 / 失败证据） ----------------
    def start_screen_record(self, time_limit: int = 180,
                            remote_path: str = "/sdcard/perf_record.mp4") -> bool:
        """后台异步录屏。显式返回是否成功启动，不再静默丢视频。"""
        cmd = [self.adb_bin, "shell", "screenrecord",
               "--bit-rate", "4000000", "--time-limit", str(time_limit), remote_path]
        try:
            self.record_process = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2)  # 等待车机端 MediaCodec 拉起
            if self.record_process.poll() is not None:
                logger.error("screenrecord 进程提前退出（exit=%s）", self.record_process.returncode)
                self.record_process = None
                return False
            self._remote_video_path = remote_path
            logger.info("后台录屏已启动（上限 %ds）", time_limit)
            return True
        except Exception as e:
            logger.error("录屏启动失败: %s", e)
            return False

    def stop_and_pull_record(self, local_path) -> bool:
        """优雅终止录屏并拉取视频（SIGINT 让 moov atom 正常落盘）。"""
        if self.record_process is None:
            logger.error("录屏未在运行，无法拉取（请检查 start_screen_record 返回值）")
            return False
        try:
            self.run_adb_cmd(["shell", "pkill", "-2", "screenrecord"])
            self.record_process = None
            time.sleep(2)
            self.run_adb_cmd(["pull", self._remote_video_path, str(local_path)])
            self.run_adb_cmd(["shell", "rm", self._remote_video_path])
            logger.info("性能视频已拉取: %s", local_path)
            return True
        except Exception as e:
            logger.error("录屏拉取失败: %s", e)
            return False

    # ---------------- 日志转储（证据桥接原料） ----------------
    def dump_logcat(self, save_path, lines: int = 5000) -> bool:
        """把设备当前 logcat 缓冲转储为文件（失败证据包的一部分）。"""
        try:
            out = self.run_adb_cmd(["shell", "logcat", "-d", "-v", "threadtime", "-t", str(lines)])
            Path(save_path).write_text(out, encoding="utf-8", errors="ignore")
            logger.info("logcat 转储完成: %s（%d 行）", save_path, len(out.splitlines()))
            return True
        except Exception as e:
            logger.error("logcat 转储失败: %s", e)
            return False
