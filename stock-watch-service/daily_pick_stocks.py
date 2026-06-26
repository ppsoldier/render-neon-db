#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日选股程序 - 三层策略：因子筛选 + 财务评分 + 综合评分
"""

import os
import sys
import time
import pickle
import re
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import pandas as pd
import numpy as np
import requests
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (
    STOCK_FILTER, SCORE_WEIGHTS, TECHNICAL_PARAMS,
    OUTPUT_DIR, DATA_CACHE_DIR, LOG_LEVEL, PERFORMANCE,
    NEWS_CONFIG, FACTOR_FILTER, LLM_CONFIG,
    USE_CACHE, REQUEST_TIMEOUT, MAX_STOCK_PAGES  # 新增
)
from market_analyzer import MarketAnalyzer
from data_collector.jiufang import JiuFangCollector, call_llm_analysis

try:
    import akshare as ak
except ImportError:
    logger.warning("akshare 未安装，消息面和财务功能将不可用")
    ak = None

import baostock as bs

# ========== 路径配置 ==========
os.makedirs(DATA_CACHE_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
BAOSTOCK_CACHE_DIR = os.path.join(DATA_CACHE_DIR, "baostock")
os.makedirs(BAOSTOCK_CACHE_DIR, exist_ok=True)

WECHAT_WEBHOOK_URL = getattr(sys.modules.get('config'), 'WECHAT_WEBHOOK_URL', '')
if not WECHAT_WEBHOOK_URL:
    WECHAT_WEBHOOK_URL = os.getenv('WECHAT_WEBHOOK_URL', '')

logger.add("daily_pick.log", rotation="1 week", level=LOG_LEVEL)


# ========== 股票代码提取器 ==========
class StockCodeExtractor:
    SECTOR_CODES = {
        'sh': ['600', '601', '603', '605', '688'],
        'sz': ['000', '001', '002', '003', '300', '301'],
        'bj': ['430', '830', '831', '832', '833', '834', '835', '836', '837', '838', '839',
               '870', '871', '872', '873', '874', '875', '876', '877', '878', '879',
               '880', '881', '882', '883', '884', '885', '886', '887', '888', '889'],
    }
    INDEX_CODES = {'000001', '000002', '000003', '000004', '000005',
                   '399001', '399002', '399003', '399004', '399005', '399006',
                   '000300', '000905', '000852'}
    _name_to_code = None

    @classmethod
    def _load_name_mapping(cls):
        if cls._name_to_code is not None:
            return cls._name_to_code
        mapping = {}
        current_file = os.path.abspath(__file__)
        analysis_dir = os.path.dirname(current_file)
        project_root = os.path.dirname(analysis_dir)
        mapping_file = os.path.join(project_root, "stock_money", "stock_mapping_full.txt")
        if not os.path.exists(mapping_file):
            logger.warning(f"映射文件不存在: {mapping_file}")
            return mapping
        try:
            with open(mapping_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    parts = line.split('->')
                    if len(parts) != 2:
                        continue
                    right_part = parts[1].strip()
                    code_parts = right_part.split(',')
                    if len(code_parts) >= 2:
                        code = code_parts[0].strip()
                        name = code_parts[1].strip()
                        name = name.replace('*', '').replace('ST', '').strip()
                        if code and name and len(code) == 6:
                            mapping[name] = code
                            if '*' in code_parts[1] or 'ST' in code_parts[1]:
                                clean_name = code_parts[1].replace('*', '').replace('ST', '').strip()
                                if clean_name != name:
                                    mapping[clean_name] = code
            logger.info(f"加载股票名称映射: {len(mapping)} 条")
            cls._name_to_code = mapping
        except Exception as e:
            logger.error(f"加载映射文件失败: {e}")
        return mapping

    @classmethod
    def is_valid_code(cls, code: str) -> bool:
        if not code or len(code) != 6:
            return False
        if code in cls.INDEX_CODES:
            return False
        for prefixes in cls.SECTOR_CODES.values():
            if code.startswith(tuple(prefixes)):
                return True
        return False

    @classmethod
    def extract_codes(cls, text: str) -> List[str]:
        if not text:
            return []
        extracted = set()
        pattern_prefix = r'(?:sh|sz|SH|SZ)([0-9]{6})'
        for match in re.findall(pattern_prefix, text):
            if cls.is_valid_code(match):
                extracted.add(match)
        pattern_bracket = r'[\(（]([0-9]{6})[\)）]'
        for match in re.findall(pattern_bracket, text):
            if cls.is_valid_code(match):
                extracted.add(match)
        pattern_boundary = r'\b([0-9]{6})\b'
        for match in re.findall(pattern_boundary, text):
            if cls.is_valid_code(match):
                extracted.add(match)
        pattern_dot = r'([0-9]{6})\.(?:SH|SZ)'
        for match in re.findall(pattern_dot, text, re.IGNORECASE):
            if cls.is_valid_code(match):
                extracted.add(match)
        name_to_code = cls._load_name_mapping()
        for name, code in name_to_code.items():
            if name in text:
                if cls.is_valid_code(code):
                    extracted.add(code)
        return list(extracted)


# ========== 新闻事件驱动 ==========
def fetch_news_with_events(minutes: int = 120) -> pd.DataFrame:
    if ak is None:
        return pd.DataFrame()
    try:
        news_df = ak.stock_news_em()
        if news_df.empty:
            return pd.DataFrame()
        content_col = '新闻内容'
        time_col = '发布时间'
        title_col = '新闻标题'
        if time_col in news_df.columns:
            news_df[time_col] = pd.to_datetime(news_df[time_col], errors='coerce')
            cutoff_time = datetime.now() - timedelta(minutes=minutes)
            news_df = news_df[news_df[time_col] >= cutoff_time]
        if news_df.empty:
            return pd.DataFrame()
        event_weights = NEWS_CONFIG.get("event_weights", {})
        source_weights = NEWS_CONFIG.get("source_weights", {})
        results = []
        for _, row in news_df.iterrows():
            content = str(row[content_col]) if pd.notna(row[content_col]) else ""
            if len(content) < 20:
                continue
            if time_col in news_df.columns:
                publish_time = row[time_col] if pd.notna(row[time_col]) else datetime.now()
            else:
                publish_time = datetime.now()
            codes = StockCodeExtractor.extract_codes(content)
            if not codes:
                continue
            matched_events = []
            total_weight = 0
            for event, weight in event_weights.items():
                if event in content:
                    matched_events.append(event)
                    total_weight += weight
            if not matched_events and codes:
                total_weight = 2
                matched_events = ["提及"]
            source = "媒体报道"
            if "公告" in content:
                source = "公司公告"
            elif "交易所" in content:
                source = "交易所"
            source_weight = source_weights.get(source, 0.8)
            if isinstance(publish_time, pd.Timestamp):
                minutes_ago = (datetime.now() - publish_time).total_seconds() / 60
            else:
                minutes_ago = 0
            decay_rate = NEWS_CONFIG.get("decay_minutes", 60)
            decay_factor = max(0.3, 1 - (minutes_ago / decay_rate) * 0.7)
            final_weight = total_weight * source_weight * decay_factor
            for code in codes:
                sector = StockCodeExtractor.get_sector(code)
                pos = content.find(code)
                surrounding = content[max(0, pos-30):min(len(content), pos+50)] if pos >= 0 else ""
                results.append({
                    'code': code, 'sector': sector, 'publish_time': publish_time,
                    'title': row.get(title_col, '')[:50] if title_col else '',
                    'content': content[:200], 'context': surrounding,
                    'events': ','.join(matched_events[:3]), 'event_weight': total_weight,
                    'final_weight': round(final_weight, 2), 'source': source,
                    'minutes_ago': round(minutes_ago, 1),
                })
        if not results:
            return pd.DataFrame()
        result_df = pd.DataFrame(results)
        summary_df = result_df.loc[result_df.groupby('code')['final_weight'].idxmax()].copy()
        summary_df['total_news_count'] = result_df.groupby('code').size().values
        return summary_df
    except Exception as e:
        logger.error(f"获取事件新闻失败: {e}")
        return pd.DataFrame()

def get_news_score(codes: List[str], minutes: int = 120) -> Dict:
    news_df = fetch_news_with_events(minutes=minutes)
    if news_df.empty:
        return {}
    result = {}
    for code in codes:
        code_str = str(code).zfill(6)
        news_row = news_df[news_df['code'] == code_str]
        if news_row.empty:
            result[code_str] = {"score": 0, "events": "", "summary": "", "news_count": 0}
        else:
            row = news_row.iloc[0]
            raw_score = row['final_weight']
            normalized_score = min(raw_score, NEWS_CONFIG.get("max_boost", 20))
            if '减持' in str(row['events']) or '警示' in str(row['events']):
                normalized_score = max(-NEWS_CONFIG.get("max_boost", 20), normalized_score)
            result[code_str] = {
                "score": normalized_score, "events": row['events'],
                "summary": row['content'][:100] + "...", "news_count": row['total_news_count'],
                "minutes_ago": row['minutes_ago'], "source": row['source']
            }
    return result


# ========== 辅助函数 ==========
def send_to_wechat(message: str) -> None:
    if not WECHAT_WEBHOOK_URL:
        return
    headers = {'Content-Type': 'application/json'}
    data = {"msgtype": "markdown", "markdown": {"content": message}}
    try:
        resp = requests.post(WECHAT_WEBHOOK_URL, json=data, headers=headers, timeout=5)
        if resp.status_code == 200:
            result = resp.json()
            if result.get('errcode') == 0:
                logger.info("微信推送成功")
            else:
                logger.warning(f"微信推送失败: {result}")
        else:
            logger.warning(f"微信推送失败: HTTP {resp.status_code}")
    except Exception as e:
        logger.error(f"微信推送异常: {e}")

def calc_sentiment(concepts: pd.DataFrame, industries: pd.DataFrame) -> dict:
    avg_c = concepts["change_pct"].mean() if not concepts.empty else 0
    avg_i = industries["change_pct"].mean() if not industries.empty else 0
    if avg_c > 2:
        mood, score = "积极", 80
    elif avg_c > 0.5:
        mood, score = "偏积极", 65
    elif avg_c > -0.5:
        mood, score = "中性", 50
    elif avg_c > -2:
        mood, score = "偏谨慎", 35
    else:
        mood, score = "谨慎", 20
    return {
        "mood": mood, "mood_score": score,
        "hot_concept_count": len(concepts), "hot_industry_count": len(industries),
        "avg_concept_change": avg_c, "avg_industry_change": avg_i,
        "top_concept": concepts.iloc[0]["concept_name"] if not concepts.empty else "N/A",
        "top_industry": industries.iloc[0]["industry_name"] if not industries.empty else "N/A",
    }


# ========== Baostock 历史数据 ==========
def get_baostock_hist(symbol: str, days: int = 250, need_login: bool = True, max_retries: int = 2) -> Optional[pd.DataFrame]:
    code = symbol.split('.')[1]
    cache_file = os.path.join(BAOSTOCK_CACHE_DIR, f"{code}_{days}.pkl")
    if os.path.exists(cache_file):
        mod_time = datetime.fromtimestamp(os.path.getmtime(cache_file))
        if mod_time.date() == datetime.now().date():
            with open(cache_file, 'rb') as f:
                return pickle.load(f)
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime('%Y-%m-%d')
    for attempt in range(max_retries):
        try:
            if need_login:
                lg = bs.login()
                if lg.error_code != '0':
                    logger.warning(f"baostock 登录失败: {lg.error_msg}")
                    return None
            rs = bs.query_history_k_data_plus(symbol, "date,open,high,low,close,volume",
                                              start_date=start_date, end_date=end_date,
                                              frequency="d", adjustflag="2")
            data_list = []
            while (rs.error_code == '0') and rs.next():
                data_list.append(rs.get_row_data())
            if need_login:
                bs.logout()
            if not data_list:
                return None
            df = pd.DataFrame(data_list, columns=['date','open','high','low','close','volume'])
            for col in ['open','high','low','close','volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)
            df = df.sort_index().tail(days)
            with open(cache_file, 'wb') as f:
                pickle.dump(df, f)
            return df
        except Exception as e:
            logger.debug(f"获取历史数据失败 {symbol} (尝试 {attempt+1}/{max_retries}): {e}")
            time.sleep(0.5)
    return None

def get_technical_indicators(symbol: str, need_login: bool = True, max_retries: int = 2) -> Optional[dict]:
    for attempt in range(max_retries):
        try:
            df = get_baostock_hist(symbol, days=250, need_login=need_login)
            if df is None or len(df) < 200:
                return None
            close = df['close']
            ma5 = close.rolling(5).mean().iloc[-1]
            ma20 = close.rolling(20).mean().iloc[-1]
            ma5_prev = close.rolling(5).mean().iloc[-2]
            ma20_prev = close.rolling(20).mean().iloc[-2]
            golden_cross = (ma5 > ma20) and (ma5_prev <= ma20_prev)
            exp_fast = close.ewm(span=12, adjust=False).mean()
            exp_slow = close.ewm(span=26, adjust=False).mean()
            macd_line = exp_fast - exp_slow
            signal_line = macd_line.ewm(span=9, adjust=False).mean()
            macd_hist = macd_line - signal_line
            macd_positive = macd_hist.iloc[-1] > 0
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs)).iloc[-1]
            high, low = df['high'], df['low']
            tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
            atr = tr.rolling(20).mean().iloc[-1]
            volatility = atr / close.iloc[-1]
            ret20 = (close.iloc[-1] - close.iloc[-21]) / close.iloc[-21] if len(close) >= 21 else None
            daily_ret = close.pct_change().dropna()
            vol60 = daily_ret.tail(60).std() if len(daily_ret) >= 60 else None
            return {
                'golden_cross': golden_cross, 'macd_positive': macd_positive, 'rsi': rsi,
                'volatility': volatility, 'ma5': ma5, 'ma20': ma20, 'close_hist': close.iloc[-1],
                'ret20': ret20, 'vol60': vol60,
            }
        except Exception as e:
            logger.debug(f"获取技术指标失败 {symbol} (尝试 {attempt+1}/{max_retries}): {e}")
            time.sleep(0.5)
    return None

def get_price_position(symbol: str, days: int = 120, need_login: bool = False) -> Optional[float]:
    df = get_baostock_hist(symbol, days=days, need_login=need_login)
    if df is None or len(df) < days * 0.8:
        return None
    high = df['high'].max()
    low = df['low'].min()
    current = df['close'].iloc[-1]
    if high == low:
        return 0.5
    return round((current - low) / (high - low), 3)


# ========== 过滤与评分 ==========
def filter_stocks(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    for col in ['market_cap', 'turnover_rate', 'volume_ratio', 'pe_ratio', 'change_pct', 'amplitude']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    min_cap = STOCK_FILTER.get('min_market_cap', 0)
    max_cap = STOCK_FILTER.get('max_market_cap', 2000)
    if 'market_cap' in df.columns:
        df = df[(df['market_cap'] >= min_cap) & (df['market_cap'] <= max_cap)]
    min_turn = STOCK_FILTER.get('min_turnover_rate', 0)
    max_turn = STOCK_FILTER.get('max_turnover_rate', 25)
    if 'turnover_rate' in df.columns:
        df = df[(df['turnover_rate'] >= min_turn) & (df['turnover_rate'] <= max_turn)]
    min_vr = STOCK_FILTER.get('min_volume_ratio', 1.5)
    if 'volume_ratio' in df.columns:
        df = df[df['volume_ratio'] >= min_vr]
    max_pe = STOCK_FILTER.get('max_pe_ratio', 200)
    min_pe = STOCK_FILTER.get('min_pe_ratio', 0)
    if 'pe_ratio' in df.columns:
        df = df[(df['pe_ratio'] >= min_pe) & (df['pe_ratio'] <= max_pe)]
    min_rise = STOCK_FILTER.get('min_rise_pct', -5)
    max_rise = STOCK_FILTER.get('max_rise_pct', 9.5)
    if 'change_pct' in df.columns:
        df = df[(df['change_pct'] >= min_rise) & (df['change_pct'] <= max_rise)]
    max_amp = TECHNICAL_PARAMS.get('max_amplitude', 15)
    if 'amplitude' in df.columns:
        df = df[df['amplitude'] <= max_amp]
    if 'change_pct' in df.columns and 'code' in df.columns and not df.empty:
        def is_limit_up(row):
            code = str(row['code'])
            change = row['change_pct']
            if code.startswith(('30', '68')):
                return change >= 19.5
            elif code.startswith('8'):
                return change >= 29.5
            else:
                return change >= 9.5
        df = df[~df.apply(is_limit_up, axis=1)]
    return df

def get_hot_stock_set(concepts_df: pd.DataFrame, industries_df: pd.DataFrame, top_n: int = 10) -> set:
    hot_codes = set()
    if not concepts_df.empty and 'stock_prodCodes' in concepts_df.columns:
        top_concepts = concepts_df.nlargest(top_n, 'change_pct')
        for _, row in top_concepts.iterrows():
            codes = row.get('stock_prodCodes', '')
            if codes:
                hot_codes.update([c.strip() for c in codes.split(',') if c.strip()])
    if not industries_df.empty and 'stock_prodCodes' in industries_df.columns:
        top_industries = industries_df.nlargest(top_n, 'change_pct')
        for _, row in top_industries.iterrows():
            codes = row.get('stock_prodCodes', '')
            if codes:
                hot_codes.update([c.strip() for c in codes.split(',') if c.strip()])
    return hot_codes

def filter_by_hot_sector_codes(df: pd.DataFrame, hot_stock_set: set) -> pd.DataFrame:
    if df.empty or not hot_stock_set:
        return df
    df_copy = df.copy()
    df_copy['code_str'] = df_copy['code'].astype(str).str.zfill(6)
    df_copy = df_copy[df_copy['code_str'].isin(hot_stock_set)]
    df_copy = df_copy.drop(columns=['code_str'])
    return df_copy

def calculate_composite_score(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    def normalize(series, reverse=False):
        if series.max() == series.min():
            return pd.Series([0.5] * len(series), index=series.index)
        s = (series - series.min()) / (series.max() - series.min())
        return 1 - s if reverse else s
    if 'change_pct' in df.columns:
        df['trend_score'] = normalize(df['change_pct'])
    if 'volume_ratio' in df.columns:
        df['volume_score'] = normalize(df['volume_ratio'])
    if 'main_inflow' in df.columns:
        df['flow_score'] = normalize(df['main_inflow'])
    if 'day5_pct' in df.columns:
        df['tech_score'] = normalize(df['day5_pct'])
    else:
        df['tech_score'] = 0.5
    df['sentiment_score'] = 0.5
    if 'amplitude' in df.columns:
        df['risk_score'] = normalize(df['amplitude'], reverse=True)
    if 'position' in df.columns:
        df['position_score'] = 1 - df['position']
    else:
        df['position_score'] = 0.5
    if 'news_score' in df.columns:
        df['news_normalized'] = (df['news_score'].clip(0, 20) / 20)
    else:
        df['news_normalized'] = 0.5
    weights = SCORE_WEIGHTS
    df['total_score'] = (
        df.get('trend_score', 0) * weights.get('trend_strength', 0.12) +
        df.get('volume_score', 0) * weights.get('volume_activity', 0.12) +
        df.get('flow_score', 0) * weights.get('capital_flow', 0.25) +
        df.get('tech_score', 0) * weights.get('technical_signal', 0.16) +
        df.get('sentiment_score', 0) * weights.get('market_sentiment', 0.08) +
        df.get('risk_score', 0) * weights.get('risk_control', 0.08) +
        df.get('position_score', 0) * weights.get('position', 0.09) +
        df.get('news_normalized', 0) * weights.get('news_event', 0.10)
    ) * 100
    df['total_score'] = df['total_score'].clip(0, 100)
    return df


def generate_report(stocks_df: pd.DataFrame, sentiment: dict, llm_analysis: str = None,
                    market_result: dict = None) -> str:
    """生成美观的选股报告（Markdown格式，适配企业微信）"""
    if stocks_df.empty:
        # 无选股结果时也显示大盘信息
        market_info = ""
        if market_result:
            market_info = f"""
            > **市场状态**：{market_result.get('market_state', '未知')}
            > **综合评分**：{market_result.get('state_score', 0)}
            > **建议仓位**：{market_result.get('position_ratio', 0.5) * 100:.0f}%
            > **操作建议**：{market_result.get('advice', '')}
            
            """
        return f"## 📭 {datetime.now().strftime('%Y-%m-%d')} 选股结果\n\n{market_info}> 今日无符合条件的股票。"

    stocks_df = stocks_df.sort_values('total_score', ascending=False).head(8)
    avg_score = stocks_df['total_score'].mean()
    best_stock = stocks_df.iloc[0]

    lines = [
        f"## 📈 {datetime.now().strftime('%Y-%m-%d')} 优选股票池",
        "",
    ]

    # 添加大盘信息
    if market_result:
        lines.extend([
            f"### 📊 大盘分析",
            f"> **市场状态**：{market_result.get('market_state', '未知')}",
            f"> **涨停/跌停**：{market_result['details']['limit_up_count']} / {market_result['details']['limit_down_count']}",
            f"> **综合评分**：{market_result.get('state_score', 0)}",
            f"> **建议仓位**：{market_result.get('position_ratio', 0.5) * 100:.0f}%",
            f"> **操作建议**：{market_result.get('advice', '')}",
            "",
        ])

    # 在大盘分析部分增加转势判断
    if market_result and "turnaround" in market_result:
        ta = market_result["turnaround"]
        if ta["is_turnaround"]:
            lines.append("> **✅ 转势信号**：波段底部确认，可逐步加仓")
        elif ta["conditions"]["advance_ratio"] >= 60:
            lines.append(f"> **🟡 转势信号**：个股普涨({ta['conditions']['advance_ratio']}%)，等待指数确认")

    lines.extend([
        f"### 📈 市场情绪",
        f"> **情绪**：{sentiment.get('mood', '中性')}　评分：{sentiment.get('mood_score', 50)}",
        f"> **热点概念**：`{sentiment.get('top_concept', 'N/A')}`",
        f"> **热点行业**：`{sentiment.get('top_industry', 'N/A')}`",
        "",
        f"**📊 本周精选** | 共 {len(stocks_df)} 只  | 平均评分：{avg_score:.1f}",
        f"**🏆 最强信号**：{best_stock['name']}（{best_stock['code']}）评分 {best_stock['total_score']:.0f} 分",
        "---"
    ])

    for idx, (_, row) in enumerate(stocks_df.iterrows(), 1):
        stars = "⭐" * min(5, int(row['total_score'] / 20) + 1)
        change = row['change_pct']
        change_symbol = "🔺" if change > 0 else "🔻"
        inflow = row.get('main_inflow', 0)
        fund_symbol = "💰" if inflow > 0.5 else ("💧" if inflow > 0 else "⚠️")
        golden = "✅" if row.get('golden_cross') else "➖"
        macd = "✅" if row.get('macd_positive') else "❌"
        fin_rating = row.get('fin_rating', 'N/A')
        fin_score = row.get('fin_score', 0)

        lines.append(f"### {idx}. {row['name']} `{row['code']}` {stars}")
        lines.append("| 项目 | 数值 | 信号 |")
        lines.append("|------|------|------|")
        lines.append(f"| 最新价 | **{row['price']:.2f}** | {change_symbol} {change:.2f}% |")
        lines.append(f"| 量比 | {row.get('volume_ratio', 0):.2f} | 主力净流入 {fund_symbol} {inflow:.2f}亿 |")
        lines.append(f"| 金叉 | {golden} | MACD>0 {macd} |")
        lines.append(f"| RSI | {row.get('rsi', 0):.1f} | 波动率 {row.get('volatility', 0):.2%} |")
        lines.append(f"| 财务质量 | **{fin_rating}** | 得分:{fin_score}/25 |")
        lines.append(
            f"| **综合评分** | **{row.get('total_score', 0):.0f}** | {'🔴' if row.get('total_score', 0) > 70 else '🟡'} |")
        lines.append("--------------------------")

    lines.append("> ⚠️ **风险提示**：以上仅为量化模型筛选结果，不构成投资建议。")
    lines.append("> 📅 投资有风险，入市需谨慎，请结合基本面独立判断。")

    if llm_analysis:
        lines.extend(["", "---", "", "## 🤖 AI 智能分析", "", llm_analysis])

    return "\n".join(lines)


# ========== 财务评分 ==========
import baostock as bs
from typing import Optional, Dict


def get_financial_score(stock_code: str, need_login: bool = True) -> int:
    """获取财务指标并评分，使用复用的登录会话"""
    symbol = f"sh.{stock_code}" if stock_code.startswith('6') else f"sz.{stock_code}"

    try:
        # 获取利润表数据（最新一期）
        profit_rs = bs.query_profit_data(code=symbol, year=2024, quarter=4)
        if profit_rs.error_code != '0':
            profit_rs = bs.query_profit_data(code=symbol, year=2024, quarter=3)
        if profit_rs.error_code != '0':
            profit_rs = bs.query_profit_data(code=symbol, year=2025, quarter=1)
        if profit_rs.error_code != '0':
            logger.debug(f"{stock_code} 无利润表数据")
            return 0

        profit_rs.next()
        profit = profit_rs.get_row_data()
        if not profit:
            return 0

        # 字段索引（基于 baostock 返回格式）
        # 索引: 8=营收同比增长(%), 6=净利润同比增长(%), 9=毛利率(%)
        revenue_yoy = float(profit[8]) if profit[8] else 0
        net_profit_yoy = float(profit[6]) if profit[6] else 0
        gross_margin = float(profit[9]) if profit[9] else 0

        # 评分规则
        score = 0
        if revenue_yoy > 10:
            score += 1
        if net_profit_yoy > 10:
            score += 1
        if gross_margin > 20:
            score += 1

        if score >= 2:
            return 1
        elif score <= 0:
            return -1
        else:
            return 0

    except Exception as e:
        logger.debug(f"财务数据获取失败 {stock_code}: {e}")
        return 0


def get_latest_report_period(symbol: str) -> tuple:
    """
    获取股票最新可用的财报年份和季度
    返回 (year, quarter) 或 (None, None)
    """
    import datetime

    current_year = datetime.datetime.now().year
    current_quarter = (datetime.datetime.now().month - 1) // 3 + 1

    # 从当前季度往前试探，找到有数据的财报
    for year in range(current_year, current_year - 2, -1):
        for quarter in range(current_quarter, 0, -1):
            try:
                rs = bs.query_profit_data(code=symbol, year=year, quarter=quarter)
                if rs.error_code == '0':
                    rs.next()
                    data = rs.get_row_data()
                    if data and len(data) > 3 and data[3]:
                        return year, quarter
            except:
                continue

    # 如果最近一年没有，尝试固定使用2024年Q4（最新的完整年报）
    return 2024, 4


def safe_float(value) -> float:
    """安全转换为float，处理None和空字符串"""
    if value is None or value == '':
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def add_financial_reference(df: pd.DataFrame) -> pd.DataFrame:
    """
    为选出的股票添加财务数据作为参考（不影响评分和选股）
    动态获取最新财报，自动识别年报/季报，并添加季度预警
    改进：增加净利润绝对值判断，细化评分范围，并显示报告期
    """
    if df.empty:
        return df

    lg = bs.login()
    if lg.error_code != '0':
        logger.warning("baostock 登录失败，无法获取财务数据")
        return df

    for idx, row in df.iterrows():
        code = str(row['code']).zfill(6)
        symbol = f"sh.{code}" if code.startswith('6') else f"sz.{code}"

        try:
            # 动态获取最新财报数据
            from datetime import datetime
            current_year = datetime.now().year

            latest_data = None
            latest_year = None
            latest_quarter = None

            for year in range(current_year, current_year - 2, -1):
                for quarter in [1, 2, 3, 4]:
                    rs = bs.query_profit_data(code=symbol, year=year, quarter=quarter)
                    if rs.error_code == '0':
                        rs.next()
                        data = rs.get_row_data()
                        if data and len(data) > 6 and data[3]:
                            latest_data = data
                            latest_year = year
                            latest_quarter = quarter
                            break
                if latest_data:
                    break

            if latest_data is None:
                logger.debug(f"{code} 无财务数据")
                # 默认值：无财务数据时设为中性
                df.loc[idx, 'fin_rating'] = "无数据"
                df.loc[idx, 'fin_score'] = 0
                continue

            # 解析数据
            try:
                roe = float(latest_data[3]) * 100 if latest_data[3] else 0
                profit_yoy = float(latest_data[4]) * 100 if latest_data[4] else 0
                revenue_yoy = float(latest_data[5]) * 100 if latest_data[5] else 0
                net_profit = float(latest_data[6]) if len(latest_data) > 6 and latest_data[6] else 0
            except (ValueError, IndexError) as e:
                logger.debug(f"{code} 数据解析失败: {e}")
                continue

            logger.info(
                f"{code} {latest_year}Q{latest_quarter}: ROE={roe:.1f}%, 营收增长={revenue_yoy:.1f}%, 利润增长={profit_yoy:.1f}%, 净利润={net_profit/100000000:.2f}亿")

            # ========== 改进的财务评分逻辑 ==========
            # 先判断净利润是否为负（绝对亏损）
            if net_profit < 0:
                # 亏损公司直接定为“很差”，财务分为 -20
                rating = "很差"
                fin_score = -20
            else:
                # 基于增长率计算基础分（范围 -5 到 10）
                score = 0
                # 营收增长
                if revenue_yoy > 20:
                    score += 2
                elif revenue_yoy > 10:
                    score += 1
                elif revenue_yoy > 0:
                    score += 0.5
                elif revenue_yoy < -10:
                    score -= 1
                # 利润增长
                if profit_yoy > 20:
                    score += 2
                elif profit_yoy > 10:
                    score += 1
                elif profit_yoy > 0:
                    score += 0.5
                elif profit_yoy < -10:
                    score -= 1
                # ROE
                if roe > 15:
                    score += 1
                elif roe > 10:
                    score += 0.5
                elif roe < 0:
                    score -= 1

                # 映射分数到 -10 ~ 25 范围
                fin_score = min(25, max(-10, score * 5))

                # 根据最终分数确定评级
                if fin_score >= 15:
                    rating = "优秀"
                elif fin_score >= 5:
                    rating = "良好"
                elif fin_score >= -5:
                    rating = "一般"
                else:
                    rating = "较差"

            # 存储基础数据
            df.loc[idx, 'report_year'] = latest_year
            df.loc[idx, 'report_quarter'] = f"Q{latest_quarter}"
            df.loc[idx, 'roe'] = f"{roe:.1f}%"
            df.loc[idx, 'revenue_yoy'] = f"{revenue_yoy:.1f}%"
            df.loc[idx, 'profit_yoy'] = f"{profit_yoy:.1f}%"
            df.loc[idx, 'fin_score'] = fin_score
            df.loc[idx, 'fin_rating'] = f"{rating}({latest_year}Q{latest_quarter})"  # 带报告期

            # 季度预警：最新季度净利润同比下降超过20%
            if profit_yoy < -20:
                df.loc[idx, 'quarterly_warning'] = f"⚠️ 季度预警：净利润同比下滑{profit_yoy:.1f}%"
            elif profit_yoy < -10:
                df.loc[idx, 'quarterly_warning'] = f"⚡ 季度关注：净利润同比下滑{profit_yoy:.1f}%"
            else:
                df.loc[idx, 'quarterly_warning'] = ""

        except Exception as e:
            logger.debug(f"获取财务数据失败 {code}: {e}")
            continue

    bs.logout()
    return df


# ========== 主函数 run ==========
def run():
    logger.info("开始每日选股扫描（三层策略）...")
    # ========== 新增：同步自选股到数据库 ==========
    try:
        from config import DB_ENABLED
        if DB_ENABLED:
            from db_manager import save_watchlist
            # 读取自选股文件（stock_money/watchlist.txt）
            watchlist_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "stock_money", "watchlist.txt")
            if os.path.exists(watchlist_path):
                with open(watchlist_path, 'r', encoding='utf-8') as f:
                    codes = [line.strip() for line in f if line.strip()]
                if codes:
                    save_watchlist(codes)
                    logger.info(f"自选股已同步到数据库 ({len(codes)} 只)")
            else:
                logger.warning(f"自选股文件不存在: {watchlist_path}")
    except Exception as e:
        logger.warning(f"同步自选股到数据库失败: {e}")

    # ========== 大盘分析 ==========
    from market_analyzer import MarketAnalyzer
    market_analyzer = MarketAnalyzer()
    market_result = market_analyzer.analyze(include_turnaround=True)
    market_state = market_result.get('market_state', '未知')
    market_score = market_result.get('state_score', 0)
    market_advice = market_result.get('advice', '')
    market_position_ratio = market_result.get('position_ratio', 0.5)

    logger.info(f"大盘状态: {market_state} (评分:{market_score})")
    logger.info(f"建议仓位: {market_position_ratio * 100:.0f}%")
    logger.info(f"操作建议: {market_advice}")

    # 单边下跌市特殊处理
    if market_state == "单边下跌市":
        logger.warning("市场处于单边下跌市，暂停选股")
        report = f"""## 📉 大盘分析

> **市场状态**：{market_state}
> **综合评分**：{market_score}
> **建议仓位**：{market_position_ratio * 100:.0f}%
> **操作建议**：{market_advice}

> ⚠️ 当前市场环境不佳，暂停选股，建议空仓观望。"""
        print(report)
        send_to_wechat(report)
        return

    collector = JiuFangCollector()
    # 根据配置决定是否使用缓存
    if USE_CACHE and collector.has_today_cache():
        logger.info("使用今日缓存数据")
        stocks_df, concepts_df, industries_df = collector.load_full_cache()
    else:
        logger.info("强制实时采集（缓存已关闭或不存在）")
        stocks_df = collector.get_hot_stocks(force_refresh=True)  # 新增参数
        concepts_df = collector.get_hot_concepts()
        industries_df = collector.get_hot_industry()
        if not stocks_df.empty:
            collector.save_full_cache(stocks_df, concepts_df, industries_df)  # 仍然保存缓存，但下次不会使用（因为USE_CACHE=False）
        else:
            logger.error("获取个股数据失败")
            return

    if stocks_df.empty:
        logger.warning("无个股数据")
        return

    logger.info(f"原始个股数量: {len(stocks_df)}")
    if 'day5PxChangeRate' in stocks_df.columns and 'day5_pct' not in stocks_df.columns:
        stocks_df['day5_pct'] = stocks_df['day5PxChangeRate']
    elif 'day5_pct' not in stocks_df.columns:
        stocks_df['day5_pct'] = stocks_df['change_pct']

    filtered = filter_stocks(stocks_df)
    logger.info(f"基础过滤后剩余: {len(filtered)}")
    sentiment = calc_sentiment(concepts_df, industries_df)
    if 'stock_prodCodes' in concepts_df.columns and 'stock_prodCodes' in industries_df.columns:
        hot_stock_set = get_hot_stock_set(concepts_df, industries_df, top_n=10)
        if hot_stock_set:
            filtered = filter_by_hot_sector_codes(filtered, hot_stock_set)
            logger.info(f"热点过滤后剩余: {len(filtered)}")

    max_stocks = PERFORMANCE.get("max_technical_stocks", 100)
    candidates = filtered.sort_values('change_pct', ascending=False).head(max_stocks)
    logger.info(f"候选股票数量: {len(candidates)}")

    # 第一层：动量+波动率筛选
    min_momentum = FACTOR_FILTER.get("min_momentum", 0.05)
    max_volatility = FACTOR_FILTER.get("max_volatility", 0.40)
    factor_qualified = []

    lg = bs.login()
    if lg.error_code != '0':
        logger.error("baostock 登录失败")
        return

    for _, row in candidates.iterrows():
        code = str(row['code']).zfill(6)
        symbol = f"sh.{code}" if code.startswith('6') else f"sz.{code}"
        indicators = get_technical_indicators(symbol, need_login=False)
        if indicators is None:
            continue
        ret20 = indicators.get('ret20', -1)
        vol60 = indicators.get('vol60', 1)
        if ret20 is not None and vol60 is not None and ret20 >= min_momentum and vol60 <= max_volatility:
            row_copy = row.copy()
            row_copy['golden_cross'] = indicators['golden_cross']
            row_copy['macd_positive'] = indicators['macd_positive']
            row_copy['rsi'] = indicators['rsi']
            row_copy['volatility'] = indicators['volatility']
            row_copy['ma5'] = indicators['ma5']
            row_copy['ma20'] = indicators['ma20']
            position = get_price_position(symbol, days=120, need_login=False)
            row_copy['position'] = position if position is not None else 0.5
            row_copy['ret20'] = ret20
            row_copy['vol60'] = vol60
            factor_qualified.append(row_copy)
    bs.logout()
    logger.info(f"第一层筛选后剩余股票: {len(factor_qualified)}")

    if not factor_qualified:
        logger.info("无股票通过第一层筛选")
        report = generate_report(pd.DataFrame(), sentiment)
        print(report)
        send_to_wechat(report)
        return

    # 第二层：综合评分（不再做财务筛选）
    final_df = pd.DataFrame(factor_qualified)

    # 消息面评分
    if NEWS_CONFIG.get("enabled", True):
        try:
            all_codes = final_df['code'].tolist()
            news_scores = get_news_score(all_codes, minutes=NEWS_CONFIG.get("minutes", 120))
            for idx, row in final_df.iterrows():
                code = str(row['code']).zfill(6)
                news_info = news_scores.get(code, {})
                news_score = news_info.get('score', 0)
                if news_score != 0:
                    final_df.loc[idx, 'news_score'] = news_score
                    final_df.loc[idx, 'news_boost'] = news_score
                    final_df.loc[idx, 'news_summary'] = f"[{news_info.get('source','媒体')}] {news_info.get('events','事件')} +{news_score:.1f}分"
        except Exception as e:
            logger.warning(f"消息面评分失败: {e}")

    final_df = calculate_composite_score(final_df)
    final_df = final_df.sort_values('total_score', ascending=False)
    final_df = final_df.head(5)  # 最终持仓不超过5只
    logger.info(f"最终选股数量: {len(final_df)}")

    # ========== 新增：获取最终选股的财务数据作为参考 ==========
    final_df = add_financial_reference(final_df)

    logger.info(f"final_df 列名: {final_df.columns.tolist()}")
    logger.info(f"final_df 前3行: {final_df[['code', 'name', 'rsi']].head(3)}")

    # LLM 分析
    llm_analysis = None
    if LLM_CONFIG.get("enable_llm", False):
        try:
            logger.info("调用 LLM 分析...")
            llm_analysis = call_llm_analysis(final_df, sentiment)
            if llm_analysis:
                logger.info("LLM 分析完成")
        except Exception as e:
            logger.warning(f"LLM 分析失败: {e}")

    report = generate_report(final_df, sentiment, llm_analysis, market_result)
    print(report)
    send_to_wechat(report)

    # 保存结果
    filename = f"selected_stocks_{datetime.now().strftime('%Y%m%d')}.csv"
    filepath = os.path.join(OUTPUT_DIR, filename)
    final_df.to_csv(filepath, index=False, encoding='utf-8-sig')
    logger.info(f"选股结果保存至 {filepath}")

    # 保存信号文件给自动交易
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stock_money_dir = os.path.join(project_root, "stock_money")
    os.makedirs(stock_money_dir, exist_ok=True)
    signal_path = os.path.join(stock_money_dir, "signals.csv")
    if not final_df.empty:
        signals_df = final_df[['code', 'name', 'price', 'total_score']].copy()
        signals_df.to_csv(signal_path, index=False, encoding='utf-8')
        logger.info(f"信号文件保存至 {signal_path}")

    # ========== 保存数据到 PostgreSQL 数据库 ==========
    try:
        from config import DB_ENABLED
        if DB_ENABLED:
            from db_manager import (
                init_database, save_market_data, save_stocks_data,
                save_concepts_data, save_industries_data, save_selected_stocks,
                save_operation_log
            )

            # 初始化数据库（如果表不存在）
            init_database()

            # 保存大盘分析数据（先删除当天记录再插入）
            if market_result:
                save_market_data(market_result, delete_existing=True)
                logger.info("大盘数据已保存到数据库")

            # 保存全量个股数据
            if stocks_df is not None and not stocks_df.empty:
                save_stocks_data(stocks_df, delete_existing=True)
                logger.info(f"个股数据已保存到数据库: {len(stocks_df)} 条")

            # 保存概念板块数据
            if concepts_df is not None and not concepts_df.empty:
                save_concepts_data(concepts_df, delete_existing=True)
                logger.info(f"概念板块数据已保存到数据库: {len(concepts_df)} 条")

            # 保存行业板块数据
            if industries_df is not None and not industries_df.empty:
                save_industries_data(industries_df, delete_existing=True)
                logger.info(f"行业板块数据已保存到数据库: {len(industries_df)} 条")

            # 保存选股结果
            if final_df is not None and not final_df.empty:
                save_selected_stocks(final_df, delete_existing=True)
                logger.info(f"选股结果已保存到数据库: {len(final_df)} 只")

            # 保存操作日志
            save_operation_log(f"每日选股完成，共选出 {len(final_df) if final_df is not None else 0} 只股票", "INFO",
                               "daily_pick")

    except ImportError as e:
        logger.warning(f"数据库模块导入失败: {e}")
    except Exception as e:
        logger.warning(f"数据库保存失败: {e}")


def get_selected_stocks(use_cache: bool = True,
                        stocks_df: pd.DataFrame = None,
                        concepts_df: pd.DataFrame = None,
                        industries_df: pd.DataFrame = None, return_llm=False) -> tuple:
    """
    供外部（GUI）调用的精选选股接口。
    返回 (selected_df, concepts_df, industries_df, sentiment, market_result)
    """
    from market_analyzer import MarketAnalyzer

    # 获取大盘分析
    market_analyzer = MarketAnalyzer()
    market_result = market_analyzer.analyze()

    from data_collector.jiufang import JiuFangCollector

    if stocks_df is not None and concepts_df is not None and industries_df is not None:
        logger.info("每日精选: 使用外部传入的数据")
    else:
        collector = JiuFangCollector()
        if use_cache and collector.has_today_cache():
            logger.info("每日精选: 使用今日缓存数据")
            stocks_df, concepts_df, industries_df = collector.load_full_cache()
        else:
            logger.info("每日精选: 从API获取数据...")
            stocks_df = collector.get_hot_stocks()
            concepts_df = collector.get_hot_concepts()
            industries_df = collector.get_hot_industry()
            if not stocks_df.empty:
                collector.save_full_cache(stocks_df, concepts_df, industries_df)

    if stocks_df.empty:
        return pd.DataFrame(), concepts_df, industries_df, {}, market_result

    if 'day5PxChangeRate' in stocks_df.columns and 'day5_pct' not in stocks_df.columns:
        stocks_df['day5_pct'] = stocks_df['day5PxChangeRate']
    elif 'day5_pct' not in stocks_df.columns:
        stocks_df['day5_pct'] = stocks_df['change_pct']

    filtered = filter_stocks(stocks_df)
    sentiment = calc_sentiment(concepts_df, industries_df)

    if 'stock_prodCodes' in concepts_df.columns and 'stock_prodCodes' in industries_df.columns:
        hot_stock_set = get_hot_stock_set(concepts_df, industries_df, top_n=10)
        if hot_stock_set:
            filtered = filter_by_hot_sector_codes(filtered, hot_stock_set)

    max_stocks = PERFORMANCE.get("max_technical_stocks", 100)
    candidates = filtered.sort_values('change_pct', ascending=False).head(max_stocks)

    # 统一登录 baostock
    lg = bs.login()
    if lg.error_code != '0':
        logger.error(f"baostock 登录失败: {lg.error_msg}")
        return pd.DataFrame(), concepts_df, industries_df, sentiment, market_result

    # 第一层筛选（动量+波动率）
    min_momentum = FACTOR_FILTER.get("min_momentum", 0.05)
    max_volatility = FACTOR_FILTER.get("max_volatility", 0.40)
    factor_qualified = []

    for _, row in candidates.iterrows():
        code = str(row['code']).zfill(6)
        symbol = f"sh.{code}" if code.startswith('6') else f"sz.{code}"
        indicators = get_technical_indicators(symbol, need_login=False)
        if indicators is None:
            continue
        ret20 = indicators.get('ret20', -1)
        vol60 = indicators.get('vol60', 1)
        if ret20 is not None and vol60 is not None:
            if ret20 >= min_momentum and vol60 <= max_volatility:
                row_copy = row.copy()
                row_copy['golden_cross'] = indicators['golden_cross']
                row_copy['macd_positive'] = indicators['macd_positive']
                row_copy['rsi'] = indicators['rsi']
                row_copy['volatility'] = indicators['volatility']
                row_copy['ma5'] = indicators['ma5']
                row_copy['ma20'] = indicators['ma20']
                position = get_price_position(symbol, days=120, need_login=False)
                row_copy['position'] = position if position is not None else 0.5
                row_copy['ret20'] = ret20
                row_copy['vol60'] = vol60
                factor_qualified.append(row_copy)

    bs.logout()

    if not factor_qualified:
        return pd.DataFrame(), concepts_df, industries_df, sentiment, market_result

    # 第二层财务筛选（暂时跳过，全部通过）
    for row in factor_qualified:
        row['fin_signal'] = 1

    final_df = pd.DataFrame(factor_qualified)

    # 消息面评分
    if NEWS_CONFIG.get("enabled", True):
        try:
            all_codes = final_df['code'].tolist()
            news_scores = get_news_score(all_codes, minutes=NEWS_CONFIG.get("minutes", 120))
            for idx, row in final_df.iterrows():
                code = str(row['code']).zfill(6)
                news_info = news_scores.get(code, {})
                news_score = news_info.get('score', 0)
                if news_score != 0:
                    final_df.loc[idx, 'news_score'] = news_score
                    final_df.loc[idx, 'news_boost'] = news_score
        except Exception as e:
            logger.warning(f"消息面评分失败: {e}")

    final_df = calculate_composite_score(final_df)
    final_df = final_df.sort_values('total_score', ascending=False)
    final_df = final_df.head(5)

    return final_df, concepts_df, industries_df, sentiment, market_result

    llm_text = None
    if return_llm and LLM_CONFIG.get("enable_llm", False):
        llm_text = call_llm_analysis(final_df, sentiment)
    return final_df, concepts_df, industries_df, sentiment, market_result, llm_text

if __name__ == "__main__":
    run()
