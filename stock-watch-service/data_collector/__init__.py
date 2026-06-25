# data_collector/__init__.py
"""
数据采集模块
提供从九方智投获取股票、板块数据的功能
"""

from .jiufang import JiuFangCollector, call_llm_analysis

__all__ = ['JiuFangCollector', 'call_llm_analysis']
