"""CAN 协议解码工具：优先 cantools 解析真实 .dbc，缺失时降级内置字典。

修复记录（对应 Phase 3 / P0-3 的"名不副实"）：
原实现只有 3 个 ID 的 mock dict，requirements 却挂着 cantools。
现在真实 .dbc 与 mock 并存：有文件用文件，无文件用字典，诚实声明来源。
"""
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 内置字典兜底（离线演示可用；真实项目应提供 signal.dbc）
MOCK_DBC: Dict[str, str] = {
    "0X3F2": "车门控制模块(BCM) - 远程解锁指令 (优先级: 中)",
    "0X0C1": "发动机控制模块(ECM) - 动力总成高优心跳包 (优先级: 极高)",
    "0X3F3": "车门控制模块(BCM) - 解锁执行状态反馈 (包含状态码)",
    "0X1A4": "车身域常规状态广播 - 车速/温度心跳 (低优先级噪音)",
    "0X2B5": "底盘域常规状态广播 - 转向/制动心跳 (低优先级噪音)",
    "0X2A1": "车窗控制模块 - 远程开窗指令",
}


class CanDecoder:
    def __init__(self, dbc_path: Optional[str] = None):
        self.dbc_path = Path(dbc_path) if dbc_path else None
        self._db = None
        self.source = "内置字典(MOCK)"
        if self.dbc_path and self.dbc_path.exists():
            try:
                import cantools
                self._db = cantools.database.load_file(str(self.dbc_path))
                self.source = f"真实DBC({self.dbc_path.name})"
                logger.info("已加载真实 DBC 文件: %s", self.dbc_path)
            except Exception as e:
                logger.warning("DBC 加载失败，降级内置字典: %s", e)

    def decode(self, can_id: str) -> str:
        """解码单个 CAN ID。can_id 形如 0x3F2。"""
        upper = can_id.upper().strip()
        if self._db is not None:
            try:
                frame_id = int(upper, 16)
                msg = self._db.get_message_by_frame_id(frame_id)
                return f"{msg.name} - {msg.comment or '无注释'} (来源: 真实DBC)"
            except Exception:
                pass
        return MOCK_DBC.get(upper, "未知报文 ID，字典中未定义")

    def decode_many(self, can_ids: List[str]) -> str:
        if not can_ids:
            return "未发现需要解码的 CAN ID。"
        seen = []
        for cid in can_ids:
            if cid.upper() not in seen:
                seen.append(cid.upper())
        lines = [f"ID {cid} 解码为: {self.decode(cid)}" for cid in seen]
        return "底层协议解码结果（来源: %s）：\n%s" % (self.source, "\n".join(lines))


def extract_can_ids(text: str) -> List[str]:
    """从日志文本中提取全部十六进制 CAN ID（供 Tool-Use 与离线模式共用）。"""
    import re
    ids = re.findall(r"0[xX][0-9a-fA-F]{1,3}", text)
    return ids
