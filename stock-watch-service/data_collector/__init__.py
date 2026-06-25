"""数据采集模块"""

from .eastmoney import EastMoneyCollector
from .tonghuashun import TongHuaShunCollector
from .jiufang import JiuFangCollector

__all__ = ["EastMoneyCollector", "TongHuaShunCollector", "JiuFangCollector"]
