#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大盘分析模块 - 判断市场状态（单边上涨/震荡/单边下跌）
新增：波段底部预判、转势确认（阳包阴、站上5日线、个股上涨比例）
"""

import baostock as bs
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from loguru import logger


class MarketAnalyzer:
    """大盘分析器，支持多指数分析和转势预判"""

    def __init__(self, index_code: str = "sh.000001"):
        self.index_code = index_code
        self.df_dict = {}
        self.advance_percent = 0
        self.market_state = "未知"
        self.state_score = 0
        self.details = {}
        self.turnaround_signal = False
        self.turnaround_reasons = []

    # ========== 数据获取 ==========
    def fetch_index_data(self, index_code: str, days: int = 250) -> Optional[pd.DataFrame]:
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        lg = bs.login()
        if lg.error_code != '0':
            logger.error(f"baostock 登录失败: {lg.error_msg}")
            return None

        rs = bs.query_history_k_data_plus(
            index_code,
            "date,open,high,low,close,volume",
            start_date=start_date, end_date=end_date,
            frequency="d", adjustflag="3"
        )

        data_list = []
        while (rs.error_code == '0') and rs.next():
            data_list.append(rs.get_row_data())
        bs.logout()

        if not data_list:
            logger.error(f"未获取到指数数据: {index_code}")
            return None

        df = pd.DataFrame(data_list, columns=['date','open','high','low','close','volume'])
        for col in ['open','high','low','close','volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        df = df.sort_index().dropna()
        return df

    def fetch_all_index_data(self, days: int = 250):
        indexes = {
            'sh': 'sh.000001',
            'sz': 'sz.399001',
            'cy': 'sz.399006'
        }
        for name, code in indexes.items():
            df = self.fetch_index_data(code, days)
            if df is not None:
                self.df_dict[name] = df
                logger.info(f"获取指数 {name} 数据 {len(df)} 条")
            else:
                logger.warning(f"无法获取指数 {name} 数据")

    # 在 market_analyzer.py 中
    def fetch_advance_percent(self) -> float:
        """获取全市场个股上涨比例，优先使用九方智投的统计数据"""
        try:
            from data_collector.jiufang import JiuFangCollector
            collector = JiuFangCollector()
            stats = collector.get_market_stats()
            # 保存详细统计供后续使用
            self.market_stats = stats
            return stats['advance_percent']
        except Exception as e:
            logger.warning(f"获取个股上涨比例失败: {e}，使用默认值0.5")
            return 0.5

    # ========== 技术指标计算 ==========
    def calculate_trend_strength(self, period: int = 60) -> float:
        if 'sh' not in self.df_dict:
            return 0
        df = self.df_dict['sh']
        if df is None or len(df) < period:
            return 0
        close = df['close'].iloc[-period:]
        if len(close) < 2:
            return 0
        x = np.arange(len(close))
        slope = np.polyfit(x, close.values, 1)[0]
        trend = slope / close.mean() * 100 if close.mean() != 0 else 0
        return trend

    def calculate_volatility(self, period: int = 60) -> float:
        if 'sh' not in self.df_dict:
            return 0
        df = self.df_dict['sh']
        if df is None or len(df) < period:
            return 0
        returns = df['close'].pct_change().dropna().iloc[-period:]
        if len(returns) < 2:
            return 0
        vol = returns.std() * np.sqrt(252) * 100
        return vol

    def calculate_ma_deviation(self, short: int = 10, long: int = 30) -> float:
        if 'sh' not in self.df_dict:
            return 0
        df = self.df_dict['sh']
        if df is None or len(df) < long:
            return 0
        close = df['close']
        ma_short = close.rolling(short).mean().iloc[-1]
        ma_long = close.rolling(long).mean().iloc[-1]
        if pd.isna(ma_short) or pd.isna(ma_long):
            return 0
        return (ma_short - ma_long) / ma_long * 100

    def check_ma_arrangement(self) -> str:
        if 'sh' not in self.df_dict:
            return "数据不足"
        df = self.df_dict['sh']
        if len(df) < 30:
            return "数据不足"
        close = df['close']
        ma5 = close.rolling(5).mean().iloc[-1]
        ma10 = close.rolling(10).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]
        ma30 = close.rolling(30).mean().iloc[-1]
        if any(pd.isna(x) for x in [ma5, ma10, ma20, ma30]):
            return "计算中"
        tolerance = 0.005
        if (ma5 > ma10 * (1 - tolerance) and
            ma10 > ma20 * (1 - tolerance) and
            ma20 > ma30 * (1 - tolerance)):
            return "多头排列"
        elif (ma5 < ma10 * (1 + tolerance) and
              ma10 < ma20 * (1 + tolerance) and
              ma20 < ma30 * (1 + tolerance)):
            return "空头排列"
        else:
            return "均线缠绕"

    def check_index_position(self, period: int = 30) -> float:
        if 'sh' not in self.df_dict:
            return 0
        df = self.df_dict['sh']
        if len(df) < period:
            return 0
        close = df['close'].iloc[-1]
        ma = df['close'].rolling(period).mean().iloc[-1]
        if pd.isna(ma):
            return 0
        return (close - ma) / ma * 100

    def check_volume_trend(self) -> float:
        if 'sh' not in self.df_dict:
            return 1.0
        df = self.df_dict['sh']
        if len(df) < 20:
            return 1.0
        vol = df['volume']
        vol_ma5 = vol.rolling(5).mean().iloc[-1]
        vol_ma20 = vol.rolling(20).mean().iloc[-1]
        if pd.isna(vol_ma5) or pd.isna(vol_ma20) or vol_ma20 == 0:
            return 1.0
        return vol_ma5 / vol_ma20

    def get_recent_return(self, days: int = 5) -> float:
        if 'sh' not in self.df_dict:
            return 0
        df = self.df_dict['sh']
        if len(df) < days + 1:
            return 0
        return (df['close'].iloc[-1] - df['close'].iloc[-days-1]) / df['close'].iloc[-days-1] * 100

    # ========== K线形态检测 ==========
    def check_candle_pattern(self) -> tuple:
        """
        检测三大指数的K线形态，采用投票机制
        返回 (形态名称, 强度分数, 详情列表)
        形态名称：'阳包阴'、'阴包阳' 或 None
        强度分数：根据成立指数数量计算（1个+15分，2个+25分，3个+35分）
        """
        if len(self.df_dict) < 2:
            return None, 0, []

        bullish_count = 0  # 阳包阴计数
        bearish_count = 0  # 阴包阳计数
        bullish_indices = []  # 记录出现阳包阴的指数
        bearish_indices = []  # 记录出现阴包阳的指数

        index_names = {
            'sh': '上证指数',
            'sz': '深证成指',
            'cy': '创业板指'
        }

        for name, df in self.df_dict.items():
            if len(df) < 2:
                continue

            yesterday = df.iloc[-2]
            today = df.iloc[-1]
            index_display = index_names.get(name, name)

            # 检测阳包阴
            if (yesterday['close'] < yesterday['open'] and
                    today['close'] > today['open'] and
                    today['close'] > yesterday['open'] and
                    today['open'] < yesterday['close']):
                bullish_count += 1
                bullish_indices.append(index_display)
                logger.info(f"{index_display} 出现【阳包阴】看涨形态")

            # 检测阴包阳
            elif (yesterday['close'] > yesterday['open'] and
                  today['close'] < today['open'] and
                  today['close'] < yesterday['open'] and
                  today['open'] > yesterday['close']):
                bearish_count += 1
                bearish_indices.append(index_display)
                logger.info(f"{index_display} 出现【阴包阳】看跌形态")

        # 投票判定（至少2个指数成立）
        if bullish_count >= 2:
            strength = 20 + (bullish_count - 2) * 5
            detail = f"阳包阴 ({bullish_count}/3)：{', '.join(bullish_indices)}"
            logger.info(f"K线形态判定：{detail}，加分 {strength}")
            return "阳包阴", strength, [detail]
        elif bearish_count >= 2:
            strength = -20 - (bearish_count - 2) * 5
            detail = f"阴包阳 ({bearish_count}/3)：{', '.join(bearish_indices)}"
            logger.info(f"K线形态判定：{detail}，减分 {abs(strength)}")
            return "阴包阳", strength, [detail]

        return None, 0, []

    def fetch_market_stats(self) -> dict:
        """获取市场统计数据"""
        try:
            from data_collector.jiufang import JiuFangCollector
            collector = JiuFangCollector()
            return collector.get_market_stats()
        except Exception as e:
            logger.warning(f"获取市场统计数据失败: {e}")
            return {
                'limit_up_count': 0,
                'limit_down_count': 0,
                'up_count': 0,
                'down_count': 0,
                'advance_percent': 0,
            }

    # ========== 转势条件判断 ==========
    def check_yang_bao_yin(self) -> bool:
        if len(self.df_dict) < 3:
            return False
        yang_bao_yin_count = 0
        for name, df in self.df_dict.items():
            if len(df) < 2:
                continue
            yesterday = df.iloc[-2]
            today = df.iloc[-1]
            condition1 = today['close'] > yesterday['open']
            condition2 = today['low'] < yesterday['close']
            condition3 = (today['close'] - yesterday['close']) / yesterday['close'] > 0.01
            if condition1 and condition2 and condition3:
                yang_bao_yin_count += 1
        return yang_bao_yin_count >= 2

    def check_above_ma5(self) -> bool:
        if len(self.df_dict) < 3:
            return False
        above_count = 0
        for name, df in self.df_dict.items():
            if len(df) < 5:
                continue
            ma5 = df['close'].rolling(5).mean().iloc[-1]
            if pd.isna(ma5):
                continue
            if df['close'].iloc[-1] > ma5:
                above_count += 1
        return above_count >= 2

    def check_advance_percent(self, threshold: float = 0.6) -> bool:
        self.advance_percent = self.fetch_advance_percent()
        return self.advance_percent >= threshold

    def check_turnaround_conditions(self) -> Tuple[bool, List[str]]:
        reasons = []
        cond1 = self.check_yang_bao_yin()
        cond2 = self.check_above_ma5()
        cond3 = self.check_advance_percent(0.6)

        reasons.append("✓ 指数阳包阴" if cond1 else "✗ 指数未出现阳包阴")
        reasons.append("✓ 指数站上5日线" if cond2 else "✗ 指数未站上5日线")
        reasons.append(f"✓ 全市场上涨个股{self.advance_percent:.1%} > 60%" if cond3 else f"✗ 全市场上涨个股{self.advance_percent:.1%} < 60%")

        turnaround = cond1 and cond2 and cond3
        return turnaround, reasons

    # ========== 主分析函数 ==========
    def analyze(self, include_turnaround: bool = True) -> Dict:
        self.fetch_all_index_data(days=250)
        if not self.df_dict:
            return {
                "market_state": "未知",
                "state_score": 0,
                "position_ratio": 0.5,
                "advice": "无法获取大盘数据",
                "details": {},
                "turnaround_signal": False,
                "turnaround_reasons": []
            }

        # 计算各项指标
        trend = self.calculate_trend_strength()
        volatility = self.calculate_volatility()
        ma_deviation = self.calculate_ma_deviation()
        ma_arrangement = self.check_ma_arrangement()
        index_position = self.check_index_position()
        recent_return = self.get_recent_return(5)
        vol_ratio = self.check_volume_trend()
        today_change = 0
        if 'sh' in self.df_dict and len(self.df_dict['sh']) >= 2:
            df = self.df_dict['sh']
            today_change = (df['close'].iloc[-1] - df['close'].iloc[-2]) / df['close'].iloc[-2] * 100

        # 获取市场统计数据
        market_stats = self.fetch_market_stats()
        # 综合评分
        score = 0
        if trend > 0.1:
            score += min(30, int(trend * 50))
        elif trend < -0.1:
            score -= min(30, int(abs(trend) * 50))

        if ma_arrangement == "多头排列":
            score += 20
        elif ma_arrangement == "空头排列":
            score -= 20

        if index_position > 3:
            score += min(20, int(index_position))
        elif index_position < -3:
            score -= min(20, int(abs(index_position)))

        if recent_return > 1:
            score += min(20, int(recent_return * 2))
        elif recent_return < -1:
            score -= min(20, int(abs(recent_return) * 2))

        if vol_ratio > 1.1:
            score += min(10, int((vol_ratio - 1) * 20))
        elif vol_ratio < 0.9:
            score -= min(10, int((0.9 - vol_ratio) * 20))

        # 单日涨幅快速反应
        if today_change > 3:
            score += 30
        elif today_change > 1.5:
            score += 15
        elif today_change < -3:
            score -= 30
        elif today_change < -1.5:
            score -= 15

        # 成交量异动确认
        if vol_ratio > 1.5 and today_change > 1.5:
            score += 10
        elif vol_ratio > 1.5 and today_change < -1.5:
            score -= 10

        # K线形态检测
        pattern, pattern_strength, pattern_details = self.check_candle_pattern()
        if pattern:
            score += pattern_strength
            self.details['candle_pattern'] = pattern
            self.details['pattern_boost'] = pattern_strength
            if pattern_details:
                self.details['pattern_details'] = pattern_details
            logger.info(f"检测到【{pattern}】形态，加分 {pattern_strength}")

        score = max(-100, min(100, score))

        # 市场状态判断
        if score >= 40:
            market_state = "单边上涨市"
            advice = "积极做多，仓位70-80%"
            position_ratio = 0.75
        elif score >= 20:
            market_state = "震荡偏强市"
            advice = "谨慎做多，仓位50-60%"
            position_ratio = 0.55
        elif score >= -20:
            market_state = "震荡市"
            advice = "高抛低吸，仓位30-50%"
            position_ratio = 0.40
        elif score >= -40:
            market_state = "震荡偏弱市"
            advice = "轻仓观望，仓位20-30%"
            position_ratio = 0.25
        else:
            market_state = "单边下跌市"
            advice = "空仓或极轻仓，仓位<10%"
            position_ratio = 0.10

        self.market_state = market_state
        self.state_score = score

        self.details = {
                "trend_strength": float(round(trend, 3)),
                "volatility": float(round(volatility, 1)),            
                "ma_deviation": float(round(ma_deviation, 2)),
                "ma_arrangement": ma_arrangement,
                "index_position": float(round(index_position, 2)),
                "recent_return": float(round(recent_return, 2)),
                "vol_ratio": float(round(vol_ratio, 2)),
                "today_change": float(round(today_change, 2)),
                "score": int(score),  # 确保是 int
                "position_ratio": float(position_ratio),
                "advice": advice,
                'limit_up_count': int(market_stats.get('limit_up_count', 0)),
                'limit_down_count': int(market_stats.get('limit_down_count', 0)),
                'up_count': int(market_stats.get('up_count', 0)),
                'down_count': int(market_stats.get('down_count', 0)),
                'advance_percent': float(market_stats.get('advance_percent', 0)),
            }
        if pattern:
            self.details["candle_pattern"] = pattern
            self.details["pattern_boost"] = pattern_strength

        result = {
            "market_state": market_state,
            "state_score": score,
            "advice": advice,
            "position_ratio": position_ratio,
            "details": self.details,
        }

        if include_turnaround:
            turnaround, reasons = self.check_turnaround_conditions()
            result["turnaround_signal"] = turnaround
            result["turnaround_reasons"] = reasons
            if turnaround:
                logger.info("检测到波段底部转势信号！")
                result["advice"] = "【转势信号】波段底部确认，可逐步加仓至40-50%"
                result["position_ratio"] = 0.45
        else:
            result["turnaround_signal"] = False
            result["turnaround_reasons"] = []

        return result

    def get_position_ratio(self) -> float:
        result = self.analyze(include_turnaround=False)
        return result.get("position_ratio", 0.5)

    def should_trade(self) -> bool:
        result = self.analyze(include_turnaround=False)
        return result.get("market_state") not in ["单边下跌市", "未知"]

    def get_buy_multiplier(self) -> float:
        result = self.analyze(include_turnaround=False)
        state = result.get("market_state", "")
        multipliers = {
            "单边上涨市": 1.2,
            "震荡偏强市": 1.0,
            "震荡市": 0.7,
            "震荡偏弱市": 0.4,
            "单边下跌市": 0.1,
            "未知": 0.5,
        }
        return multipliers.get(state, 0.7)


def get_market_analysis(include_turnaround: bool = True) -> Dict:
    analyzer = MarketAnalyzer()
    return analyzer.analyze(include_turnaround=include_turnaround)


if __name__ == "__main__":
    result = get_market_analysis(include_turnaround=True)
    print("=" * 60)
    print("大盘分析结果")
    print("=" * 60)
    print(f"市场状态: {result['market_state']}")
    print(f"综合评分: {result['state_score']}")
    print(f"建议仓位: {result['position_ratio']*100:.0f}%")
    print(f"操作建议: {result['advice']}")
    if result.get('turnaround_signal'):
        print("\n【波段底部转势信号已确认】")
        for r in result['turnaround_reasons']:
            print(f"  {r}")
    else:
        print("\n转势条件未满足:")
        for r in result['turnaround_reasons']:
            print(f"  {r}")
    print("\n详细指标:")
    for k, v in result['details'].items():
        print(f"  {k}: {v}")
