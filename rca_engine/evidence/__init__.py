# 证据适配器层：轨A（UI/座舱域）与轨B（车联网链路域）
from .base import LogEvent, AlignStats          # noqa: F401
from .telematics_evidence import TelematicsEvidenceParser   # noqa: F401
from .ui_evidence import UIEvidenceParser       # noqa: F401
