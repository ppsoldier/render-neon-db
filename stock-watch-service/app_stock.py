import os
import sys
import asyncio
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import asyncpg
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager
import logging
import requests
import hashlib
import time
import json
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
import re
import subprocess
import threading
import uuid

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("stock-watch")

# ========== 股票名称映射表加载 ==========
STOCK_MAPPING_FILE = os.path.join(os.path.dirname(__file__), "stock_mapping_full.txt")
STOCK_MAPPING = {}           # 代码 -> 名称
STOCK_MAPPING_BY_CODE = {}   # 代码 -> 名称
STOCK_MAPPING_BY_NAME = {}   # 名称 -> 代码
STOCK_MAPPING_BY_PY = {}     # 拼音首字母 -> 代码列表

# ========== 数据库配置 ==========
DB_HOST = os.environ.get("DB_HOST", "ep-rapid-frog-ani7chkm.c-6.us-east-1.aws.neon.tech")
DB_USER = os.environ.get("DB_USER", "neondb_owner")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "npg_b1QR9lMdusev")
DB_NAME = os.environ.get("DB_NAME", "neondb")
DB_PORT = int(os.environ.get("DB_PORT", 5432))
SCHEMA_NAME = os.environ.get("DB_SCHEMA", "stock_data")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?sslmode=require"
SYNC_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

TABLE_MARKET = f"{SCHEMA_NAME}.market_data"
TABLE_STOCKS = f"{SCHEMA_NAME}.stocks_data"
TABLE_CONCEPTS = f"{SCHEMA_NAME}.concepts_data"
TABLE_INDUSTRIES = f"{SCHEMA_NAME}.industries_data"
TABLE_SELECTED = f"{SCHEMA_NAME}.selected_stocks"
TABLE_WATCHLIST = f"{SCHEMA_NAME}.watchlist"
TABLE_CURRENT_POSITIONS = f"{SCHEMA_NAME}.current_positions"

# ========== 同步数据库引擎 ==========
_sync_engine = None
def get_sync_engine():
    global _sync_engine
    if _sync_engine is None:
        _sync_engine = create_engine(SYNC_DATABASE_URL)
    return _sync_engine

# ========== 异步数据库连接池 ==========
db_pool = None
async def get_db():
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    return db_pool

# ========== 数据库初始化 ==========
async def init_database():
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA_NAME}")
        await conn.execute(f"SET search_path TO {SCHEMA_NAME}")
        logger.info(f"数据库初始化完成 (Schema: {SCHEMA_NAME})")

# ========== 数据保存函数 ==========
async def save_market_data(market_result: dict, data_date: str = None):
    if data_date is None:
        data_date = datetime.now().strftime("%Y-%m-%d")
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute(f"DELETE FROM {TABLE_MARKET} WHERE date = $1", data_date)
        details = market_result.get('details', {})
        await conn.execute(f"""
            INSERT INTO {TABLE_MARKET} 
            (date, market_state, market_score, position_ratio, advice, trend_strength, 
             volatility, ma_deviation, ma_arrangement, index_position, recent_return, vol_ratio)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        """,
            data_date,
            market_result.get('market_state'),
            market_result.get('state_score'),
            market_result.get('position_ratio'),
            market_result.get('advice'),
            details.get('trend_strength'),
            details.get('volatility'),
            details.get('ma_deviation'),
            details.get('ma_arrangement'),
            details.get('index_position'),
            details.get('recent_return'),
            details.get('vol_ratio')
        )
    logger.info(f"市场数据已保存: {data_date}")

async def save_selected_stocks(selected_df: pd.DataFrame, data_date: str = None):
    if selected_df.empty:
        return
    if data_date is None:
        data_date = datetime.now().strftime("%Y-%m-%d")
    engine = get_sync_engine()
    with engine.connect() as conn:
        conn.execute(text(f"DELETE FROM {TABLE_SELECTED} WHERE date = :date"), {"date": data_date})
        conn.commit()
    df = selected_df.copy()
    df['date'] = data_date
    cols = ['date', 'code', 'name', 'price', 'change_pct', 'total_score', 'advice', 'fin_rating']
    existing_cols = [c for c in cols if c in df.columns]
    df = df[existing_cols]
    df.to_sql(TABLE_SELECTED.split('.')[1], engine, schema=SCHEMA_NAME, if_exists='append', index=False)
    logger.info(f"选股结果已保存: {len(df)} 只")

# ========== 九方智投签名算法 ==========
def generate_signature(listed_sector, sort_field, sort_type, timestamp, page):
    base_string = f"sjdxfnqogbzoun13d971ckh8p{listed_sector}{page}20{sort_field}{sort_type}{timestamp}"
    return hashlib.md5(base_string.encode()).hexdigest()

def get_sector_signature(time_stamp):
    base_string = f"sjdxfnqogbzoun13d971ckh8p{time_stamp}"
    return hashlib.md5(base_string.encode()).hexdigest()

def _gen_updown_sign(timestamp: str) -> str:
    secret = "sjdxfnqogbzoun13d971ckh8p"
    return hashlib.md5(f"{secret}{timestamp}".encode()).hexdigest()

# ========== 实时数据采集函数 ==========
async def fetch_stock_rank(sort_type: str, max_pages: int = 3):
    stock_rank = []
    for page in range(1, max_pages + 1):
        timestamp = str(int(time.time() * 1000))
        params = {
            'pageNum': str(page),
            'pageSize': '20',
            'listedSector': '0',
            'sortField': 'pxChangeRate',
            'sortType': sort_type,
        }
        signature = generate_signature(
            listed_sector=params['listedSector'],
            sort_field=params['sortField'],
            sort_type=params['sortType'],
            timestamp=timestamp,
            page=page
        )
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'origin': 'https://www.9fzt.com',
            'referer': 'https://www.9fzt.com/',
            'signature': signature,
            'timestamp': timestamp,
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }
        url = 'https://api-hq.chongnengjihua.com/finance/api/2/stock/a/rank/list'
        try:
            response = requests.get(url=url, params=params, headers=headers, timeout=10)
            if response.status_code != 200:
                logger.warning(f"股票排行接口状态码异常: {response.status_code}")
                break
            try:
                data = response.json()
            except ValueError:
                logger.error(f"股票排行接口返回非JSON内容: {response.text[:200]}")
                break
            if not data or 'data' not in data or 'infos' not in data['data']:
                logger.debug(f"第{page}页无有效数据，停止翻页")
                break
            infos = data['data']['infos']
            if not infos:
                break
            for item in infos:
                price = item.get('closePx')
                if price is None:
                    price = item.get('lastPx', 0)
                if price is None:
                    price = 0
                change_raw = item.get('pxChangeRate', 0)
                change_percent = round(float(change_raw) * 100, 2) if change_raw else 0
                stock_rank.append({
                    "stock_code": item.get('symbol', ''),
                    "stock_name": item.get('prodName', ''),
                    "price": float(price) if price else 0,
                    "change_percent": change_percent,
                    "volume": item.get('businessAmount', 0),
                    "amount": item.get('businessBalance', 0)
                })
            await asyncio.sleep(0.5)
        except requests.exceptions.Timeout:
            logger.error(f"股票排行接口请求超时 (page={page})")
            break
        except Exception as e:
            logger.error(f"获取股票排行未知错误 (page={page}): {e}")
            break
    return stock_rank

async def fetch_sector_rank(hq_type_code: str):
    sector_rank = []
    for page in range(1, 3):
        timestamp = str(int(time.time() * 1000))
        params = {
            'hqTypeCode': hq_type_code,
            'sortFlag': 'true',
            'sortFields': 'pxChangeRate',
            'pageNum': page,
            'pageSize': '30',
        }
        sign = get_sector_signature(timestamp)
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'origin': 'https://stock.9fzt.com',
            'referer': 'https://stock.9fzt.com/',
            'signature': sign,
            'timestamp': timestamp,
            'user-agent': 'Mozilla/5.0...',
        }
        url = 'https://hq.chongnengjihua.com/rjhy-quote-sector/api/1/pc/plate/block/quote/list'
        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            if response.status_code != 200:
                break
            try:
                data = response.json()
            except ValueError:
                logger.error(f"板块排行接口返回非JSON: {response.text[:200]}")
                break
            if not data or 'data' not in data or 'plate' not in data['data']:
                break
            plates = data['data']['plate']
            for item in plates:
                last_px = item.get('LastPx', 0)
                px_change_rate = item.get('PxChangeRate', 0)
                sector_rank.append({
                    "sector_code": item.get('ProdCode', ''),
                    "sector_name": item.get('ProdName', ''),
                    "price": round(last_px / 1000, 2) if last_px else 0,
                    "change_percent": round(px_change_rate / 100, 2) if px_change_rate else 0
                })
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"获取板块排行错误: {e}")
            break
    return sector_rank

# ========== FastAPI 应用 ==========
app = FastAPI(title="股票看盘系统", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== 健康检查 ==========
@app.get("/")
async def root():
    return {"service": "股票看盘系统", "status": "running", "version": "2.0.0"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "stock-watch"}

# ========== 实时行情辅助函数 ==========
def fetch_realtime_quotes(stock_codes):
    if not stock_codes:
        return {}
    symbols = []
    code_map = {}
    for code in stock_codes:
        code_str = str(code)
        if code_str.startswith('sh') or code_str.startswith('sz'):
            sym = code_str
        elif code_str.startswith('6'):
            sym = f"sh{code_str}"
        elif code_str.startswith('0') or code_str.startswith('3'):
            sym = f"sz{code_str}"
        else:
            sym = f"sh{code_str}"
        symbols.append(sym)
        code_map[sym] = code_str
    all_quotes = {}
    batch_size = 50
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i+batch_size]
        url = f"http://hq.sinajs.cn/list={','.join(batch)}"
        headers = {"Referer": "http://finance.sina.com.cn"}
        try:
            resp = requests.get(url, headers=headers, timeout=5)
            resp.encoding = 'gbk'
            lines = resp.text.strip().split('\n')
            for line in lines:
                if '="' not in line:
                    continue
                match = re.search(r'hq_str_(s[hz]\d{6})', line)
                if not match:
                    continue
                full_sym = match.group(1)
                parts = line.split('="')[1].split(',')
                if len(parts) < 5:
                    continue
                name = parts[0]
                try:
                    last_close = float(parts[2])
                    current = float(parts[3])
                    if last_close != 0:
                        change_pct = round((current - last_close) / last_close * 100, 2)
                    else:
                        change_pct = 0.0
                except (ValueError, IndexError):
                    current = 0.0
                    change_pct = 0.0
                original_code = code_map.get(full_sym, full_sym[2:])
                all_quotes[original_code] = {
                    "price": current,
                    "change_pct": change_pct,
                    "name": name
                }
        except Exception as e:
            logger.error(f"新浪接口请求失败: {e}")
            continue
    return all_quotes

@app.get("/api/realtime/market-stats")
async def get_realtime_market_stats():
    timestamp = str(int(time.time() * 1000))
    headers = {
        "accept": "*/*",
        "origin": "https://www.9fzt.com",
        "referer": "https://www.9fzt.com/",
        "signature": _gen_updown_sign(timestamp),
        "timestamp": timestamp,
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    url = "https://api-hq.chongnengjihua.com/finance/api/1/stock/up/down/distributed"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        logger.info(f"实时市场统计接口返回: {data}")
        if data.get('code') == 1 and data.get('data'):
            result = data['data']
            up_cnt = result.get('up', 0)
            down_cnt = result.get('down', 0)
            flat_cnt = result.get('flat', 0)
            total = up_cnt + down_cnt + flat_cnt
            limit_up = result.get('upLimit', 0)
            limit_down = result.get('downLimit', 0)
            advance_percent = round(up_cnt / total * 100, 2) if total > 0 else 0
            return {
                "code": 200,
                "data": {
                    "total": total,
                    "up_count": up_cnt,
                    "down_count": down_cnt,
                    "flat_count": flat_cnt,
                    "limit_up_count": limit_up,
                    "limit_down_count": limit_down,
                    "advance_percent": advance_percent
                },
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        else:
            logger.warning(f"实时市场统计接口返回异常: {data}")
            return _empty_market_stats()
    except Exception as e:
        logger.error(f"获取实时市场统计数据失败: {e}")
        return _empty_market_stats()

def _empty_market_stats():
    return {
        "code": 200,
        "data": {
            "total": 0, "up_count": 0, "down_count": 0, "flat_count": 0,
            "limit_up_count": 0, "limit_down_count": 0, "advance_percent": 0
        }
    }

@app.get("/api/realtime/ranks")
async def get_realtime_ranks(rank_type: str = Query("up")):
    sort_type = '0' if rank_type == 'up' else '1'
    data = await fetch_stock_rank(sort_type)
    return {"code": 200, "data": data, "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "source": "九方智投"}

@app.get("/api/realtime/industry")
async def get_realtime_industry():
    try:
        industries = await fetch_sector_rank('HY')
        formatted = [{
            "industry_code": item.get("sector_code", ""),
            "industry_name": item.get("sector_name", ""),
            "change_percent": item.get("change_percent", 0)
        } for item in industries]
        return {"code": 200, "message": "success", "data": formatted}
    except Exception as e:
        logger.error(f"获取行业板块错误: {e}")
        return {"code": 500, "message": str(e), "data": []}

@app.get("/api/realtime/concept")
async def get_realtime_concept():
    try:
        concepts = await fetch_sector_rank('GN')
        formatted = [{
            "concept_code": item.get("sector_code", ""),
            "concept_name": item.get("sector_name", ""),
            "change_percent": item.get("change_percent", 0)
        } for item in concepts]
        return {"code": 200, "message": "success", "data": formatted}
    except Exception as e:
        logger.error(f"获取概念板块错误: {e}")
        return {"code": 500, "message": str(e), "data": []}

@app.get("/api/market/limit-stats")
async def get_limit_stats():
    try:
        stats = await get_realtime_market_stats()
        if stats.get('code') == 200:
            data = stats.get('data', {})
            limit_up = data.get('limit_up_count', 0)
            limit_down = data.get('limit_down_count', 0)
            sentiment = 50 + (limit_up - limit_down) * 2
            sentiment = max(0, min(100, sentiment))
            return {
                "limit_up_count": limit_up,
                "limit_down_count": limit_down,
                "sentiment": sentiment,
                "source": "realtime"
            }
        return {"limit_up_count": 0, "limit_down_count": 0, "sentiment": 50, "source": "default"}
    except Exception as e:
        logger.error(f"获取涨跌停统计错误: {e}")
        return {"limit_up_count": 0, "limit_down_count": 0, "sentiment": 50}

# ========== 股票映射加载 ==========
def load_stock_mapping():
    global STOCK_MAPPING, STOCK_MAPPING_BY_CODE, STOCK_MAPPING_BY_NAME, STOCK_MAPPING_BY_PY
    if not os.path.exists(STOCK_MAPPING_FILE):
        logger.warning(f"股票映射文件不存在: {STOCK_MAPPING_FILE}")
        return
    try:
        with open(STOCK_MAPPING_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or line.startswith('='):
                    continue
                parts = line.split('->')
                if len(parts) != 2:
                    continue
                py_code = parts[0].strip().upper()
                right_part = parts[1].strip()
                right_parts = right_part.split(',')
                if len(right_parts) >= 2:
                    code = right_parts[0].strip()
                    name = right_parts[1].strip()
                    price = right_parts[2].strip() if len(right_parts) > 2 else ''
                    name = name.replace('*', '').replace('ST', '').strip()
                    STOCK_MAPPING[code] = name
                    STOCK_MAPPING_BY_CODE[code] = name
                    STOCK_MAPPING_BY_NAME[name] = code
                    if py_code not in STOCK_MAPPING_BY_PY:
                        STOCK_MAPPING_BY_PY[py_code] = []
                    STOCK_MAPPING_BY_PY[py_code].append({
                        "code": code,
                        "name": name,
                        "price": price
                    })
        logger.info(f"加载股票映射完成: 代码 {len(STOCK_MAPPING_BY_CODE)} 只, 拼音 {len(STOCK_MAPPING_BY_PY)} 条")
    except Exception as e:
        logger.error(f"加载股票映射失败: {e}")

def search_stock_by_keyword(keyword: str, limit: int = 20):
    results = []
    keyword_upper = keyword.strip().upper()
    keyword_lower = keyword.strip().lower()
    if not keyword_upper:
        return results
    if keyword_upper in STOCK_MAPPING_BY_PY:
        for stock in STOCK_MAPPING_BY_PY[keyword_upper]:
            results.append({
                "stock_code": stock["code"],
                "stock_name": stock["name"],
                "price": stock.get("price", "")
            })
        if len(results) >= limit:
            return results[:limit]
    if keyword_lower in STOCK_MAPPING_BY_CODE:
        results.append({
            "stock_code": keyword_lower,
            "stock_name": STOCK_MAPPING_BY_CODE[keyword_lower],
            "price": ""
        })
        if len(results) >= limit:
            return results[:limit]
    for code, name in STOCK_MAPPING_BY_CODE.items():
        if code.startswith(keyword_lower):
            results.append({"stock_code": code, "stock_name": name, "price": ""})
            if len(results) >= limit:
                break
    if len(results) >= limit:
        return results[:limit]
    for code, name in STOCK_MAPPING_BY_CODE.items():
        if keyword_lower in name.lower():
            results.append({"stock_code": code, "stock_name": name, "price": ""})
            if len(results) >= limit:
                break
    seen = set()
    unique_results = []
    for r in results:
        if r["stock_code"] not in seen:
            seen.add(r["stock_code"])
            unique_results.append(r)
    return unique_results[:limit]

load_stock_mapping()

@app.get("/api/stock/search")
async def search_stock(keyword: str = Query(..., description="搜索关键词")):
    try:
        results = search_stock_by_keyword(keyword)
        if results:
            return {"code": 200, "data": results, "message": "success", "source": "mapping"}
        engine = get_sync_engine()
        with engine.connect() as conn:
            latest = conn.execute(text(f"SELECT MAX(date) FROM {TABLE_STOCKS}")).fetchone()[0]
            if latest:
                rows = conn.execute(
                    text(f"""
                        SELECT DISTINCT code, name
                        FROM {TABLE_STOCKS}
                        WHERE date = :latest
                          AND (code ILIKE :kw OR name ILIKE :kw)
                        LIMIT 20
                    """),
                    {"latest": latest, "kw": f"%{keyword}%"}
                ).fetchall()
                data = [{"stock_code": r[0], "stock_name": r[1], "price": ""} for r in rows]
                if data:
                    return {"code": 200, "data": data, "message": "success", "source": "database"}
        return {"code": 200, "data": [], "message": "未找到相关股票"}
    except Exception as e:
        logger.error(f"搜索股票错误: {e}")
        return {"code": 500, "message": str(e), "data": []}

# ========== 选股结果接口 ==========
@app.get("/api/stock/picks")
async def get_stock_picks():
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        engine = get_sync_engine()
        with engine.connect() as conn:
            result = conn.execute(
                text(f"""
                    SELECT code, name, price, change_pct, total_score, advice, fin_rating, date
                    FROM {TABLE_SELECTED}
                    WHERE date = :date
                    ORDER BY total_score DESC
                """),
                {"date": today}
            )
            rows = result.fetchall()
        if not rows:
            return {"code": 200, "data": [], "has_data": False, "msg": "今日尚无选股数据"}
        picks = []
        for row in rows:
            picks.append({
                "code": row[0],
                "name": row[1],
                "price": float(row[2]) if row[2] else 0,
                "change_pct": float(row[3]) if row[3] else 0,
                "total_score": float(row[4]) if row[4] else 0,
                "advice": row[5] or '',
                "fin_rating": row[6] or '',
                "date": str(row[7]) if row[7] else ''
            })
        return {"code": 200, "data": picks, "has_data": True}
    except Exception as e:
        logger.error(f"获取选股结果错误: {e}")
        return {"code": 500, "message": str(e), "data": []}

@app.get("/api/stock/market")
async def get_market_data():
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        engine = get_sync_engine()
        with engine.connect() as conn:
            result = conn.execute(
                text(f"""
                    SELECT market_state, market_score, position_ratio, advice, 
                           trend_strength, volatility, ma_arrangement,
                           limit_up_count, limit_down_count, up_count, down_count, advance_percent
                    FROM {TABLE_MARKET}
                    WHERE date = :date
                """),
                {"date": today}
            )
            row = result.fetchone()
        if not row:
            return {
                "code": 200,
                "data": {
                    "market_state": "震荡市",
                    "market_score": 50,
                    "position_ratio": 0.5,
                    "advice": "控制仓位，高抛低吸",
                    "trend_strength": 0,
                    "volatility": 0,
                    "ma_arrangement": "震荡",
                    "limit_up_count": 0,
                    "limit_down_count": 0,
                    "up_count": 0,
                    "down_count": 0,
                    "advance_percent": 0
                },
                "msg": "暂无市场数据"
            }
        market_data = {
            "market_state": row[0] or "震荡市",
            "market_score": row[1] if row[1] is not None else 50,
            "position_ratio": float(row[2]) if row[2] else 0.5,
            "advice": row[3] or "控制仓位，高抛低吸",
            "trend_strength": float(row[4]) if row[4] else 0,
            "volatility": float(row[5]) if row[5] else 0,
            "ma_arrangement": row[6] or "震荡",
            "limit_up_count": row[7] or 0,
            "limit_down_count": row[8] or 0,
            "up_count": row[9] or 0,
            "down_count": row[10] or 0,
            "advance_percent": float(row[11]) if row[11] else 0
        }
        return {"code": 200, "data": market_data}
    except Exception as e:
        logger.error(f"获取市场数据错误: {e}")
        return {"code": 500, "message": str(e), "data": None}

@app.get("/api/stock/sentiment")
async def get_sentiment_data():
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        engine = get_sync_engine()
        with engine.connect() as conn:
            concepts_result = conn.execute(
                text(f"""
                    SELECT concept_name, change_pct, leading_stock
                    FROM {TABLE_CONCEPTS}
                    WHERE date = :date
                    ORDER BY change_pct DESC
                    LIMIT 5
                """),
                {"date": today}
            )
            concepts = []
            for row in concepts_result.fetchall():
                concepts.append({
                    "name": row[0],
                    "change_pct": float(row[1]) if row[1] else 0,
                    "leading_stock": row[2] or ''
                })
        with engine.connect() as conn:
            industries_result = conn.execute(
                text(f"""
                    SELECT industry_name, change_pct, leading_stock
                    FROM {TABLE_INDUSTRIES}
                    WHERE date = :date
                    ORDER BY change_pct DESC
                    LIMIT 5
                """),
                {"date": today}
            )
            industries = []
            for row in industries_result.fetchall():
                industries.append({
                    "name": row[0],
                    "change_pct": float(row[1]) if row[1] else 0,
                    "leading_stock": row[2] or ''
                })
        return {"code": 200, "data": {"concepts": concepts, "industries": industries}}
    except Exception as e:
        logger.error(f"获取情绪数据错误: {e}")
        return {"code": 500, "message": str(e), "data": {"concepts": [], "industries": []}}

# ========== 自选股管理接口 ==========
@app.get("/api/watchlist")
async def get_watchlist():
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT code, name FROM stock_data.watchlist ORDER BY id ASC")
        if not rows:
            return {"code": 200, "data": [], "message": "暂无自选股"}
        codes = [row['code'] for row in rows]
        quotes = fetch_realtime_quotes(codes)
        result = []
        for row in rows:
            code = row['code']
            db_name = row['name']
            quote = quotes.get(code, {})
            if not quote and (code.startswith('sh') or code.startswith('sz')):
                alt_code = code[2:]
                quote = quotes.get(alt_code, {})
            name = quote.get('name') if quote.get('name') else (db_name if db_name else code)
            result.append({
                "stock_code": code,
                "stock_name": name,
                "price": quote.get('price', 0),
                "change_pct": quote.get('change_pct', 0)
            })
        return {"code": 200, "data": result, "message": "success"}
    except Exception as e:
        logger.error(f"获取自选股列表错误: {e}")
        return {"code": 500, "message": str(e), "data": []}

@app.post("/api/watchlist")
async def add_watchlist(request: Request):
    try:
        body = await request.json()
        stock_code = body.get('stock_code')
        stock_name = body.get('stock_name')
        if not stock_code or not stock_name:
            return {"code": 400, "message": "股票代码和名称不能为空"}
        pool = await get_db()
        async with pool.acquire() as conn:
            existing = await conn.fetchrow("SELECT code FROM stock_data.watchlist WHERE code = $1", stock_code)
            if existing:
                return {"code": 400, "message": "该股票已在自选股中"}
            await conn.execute("""
                INSERT INTO stock_data.watchlist (code, name, added_date)
                VALUES ($1, $2, CURRENT_DATE)
            """, stock_code, stock_name)
        return {"code": 200, "message": "添加成功"}
    except Exception as e:
        logger.error(f"添加自选股错误: {e}")
        return {"code": 500, "message": str(e)}

@app.delete("/api/watchlist")
async def delete_watchlist(stock_code: str = Query(..., description="股票代码")):
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            result = await conn.execute("DELETE FROM stock_data.watchlist WHERE code = $1", stock_code)
        if result == "DELETE 1":
            return {"code": 200, "message": "删除成功"}
        else:
            return {"code": 404, "message": "自选股不存在"}
    except Exception as e:
        logger.error(f"删除自选股错误: {e}")
        return {"code": 500, "message": str(e)}

# ========== 持仓管理接口 ==========
@app.get("/api/holdings")
async def get_holdings():
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT code, name, quantity, cost_price
                FROM stock_data.current_positions
                ORDER BY code
            """)
        if not rows:
            return {"code": 200, "data": [], "stats": {"total_value":0, "total_cost":0, "total_pnl":0, "total_pnl_pct":0}}
        codes = [row['code'] for row in rows]
        quotes = fetch_realtime_quotes(codes)
        holdings = []
        total_mv = 0.0
        total_cost = 0.0
        for row in rows:
            code = row['code']
            name = row['name'] or code
            qty = float(row['quantity'])
            cost = float(row['cost_price'])
            quote = quotes.get(code, {})
            price = float(quote.get('price', cost))
            change = float(quote.get('change_pct', 0))
            display_name = quote.get('name') or name
            market_value = price * qty
            cost_sum = cost * qty
            pnl = market_value - cost_sum
            pnl_pct = (pnl / cost_sum * 100) if cost_sum else 0.0
            holdings.append({
                "code": code,
                "name": display_name,
                "quantity": qty,
                "cost_price": round(cost, 2),
                "current_price": round(price, 2),
                "market_value": round(market_value, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "change_pct": change
            })
            total_mv += market_value
            total_cost += cost_sum
        total_pnl = total_mv - total_cost
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0.0
        return {
            "code": 200,
            "data": holdings,
            "stats": {
                "total_value": round(total_mv, 2),
                "total_cost": round(total_cost, 2),
                "total_pnl": round(total_pnl, 2),
                "total_pnl_pct": round(total_pnl_pct, 2)
            }
        }
    except Exception as e:
        logger.error(f"持仓接口错误: {e}")
        return {"code": 500, "message": str(e), "data": [], "stats": {}}

@app.post("/api/holdings")
async def add_holding(request: Request):
    try:
        body = await request.json()
        code = body.get('code')
        name = body.get('name')
        quantity = int(body.get('quantity', 0))
        cost_price = float(body.get('cost_price', 0))
        if not code or not name:
            return {"code": 400, "message": "缺少必要参数"}
        if quantity <= 0 or cost_price <= 0:
            return {"code": 400, "message": "数量和成本价必须大于0"}
        engine = get_sync_engine()
        current_price = cost_price
        try:
            up_ranks = await fetch_stock_rank('0')
            down_ranks = await fetch_stock_rank('1')
            all_stocks = {s['stock_code']: s for s in up_ranks + down_ranks}
            if code in all_stocks:
                current_price = all_stocks[code].get('price', cost_price)
        except:
            pass
        market_value = current_price * quantity
        pnl = market_value - (cost_price * quantity)
        pnl_pct = round((pnl / (cost_price * quantity)) * 100, 2) if cost_price * quantity > 0 else 0
        with engine.connect() as conn:
            existing = conn.execute(text(f"SELECT code FROM {TABLE_CURRENT_POSITIONS} WHERE code = :code"), {"code": code}).fetchone()
            if existing:
                return {"code": 400, "message": "该股票已在持仓中"}
            conn.execute(
                text(f"""
                    INSERT INTO {TABLE_CURRENT_POSITIONS} 
                    (code, name, quantity, cost_price, market_price, 
                     market_value, pnl, pnl_pct, updated_at, created_at)
                    VALUES (:code, :name, :quantity, :cost_price, :current_price,
                            :market_value, :pnl, :pnl_pct, NOW(), NOW())
                """),
                {
                    "code": code, "name": name,
                    "quantity": quantity, "cost_price": cost_price, "current_price": current_price,
                    "market_value": round(market_value, 2), 
                    "pnl": round(pnl, 2), 
                    "pnl_pct": pnl_pct
                }
            )
            conn.commit()
        return {"code": 200, "message": "添加成功"}
    except Exception as e:
        logger.error(f"添加持仓错误: {e}")
        return {"code": 500, "message": str(e)}

# 统一删除持仓（不带 user_id，因为是全局共享）
@app.delete("/api/holdings")
async def delete_holding(stock_code: str = Query(..., description="股票代码")):
    try:
        engine = get_sync_engine()
        with engine.connect() as conn:
            conn.execute(text(f"DELETE FROM {TABLE_CURRENT_POSITIONS} WHERE code = :code"), {"code": stock_code})
            conn.commit()
        return {"code": 200, "message": "删除成功"}
    except Exception as e:
        logger.error(f"删除持仓错误: {e}")
        return {"code": 500, "message": str(e)}

@app.put("/api/holdings")
async def update_holding(request: Request):
    try:
        body = await request.json()
        code = body.get('code')
        action = body.get('action')
        quantity = int(body.get('quantity', 0))
        price = float(body.get('price', 0))
        if not code:
            return {"code": 400, "message": "缺少必要参数"}
        if quantity <= 0 or price <= 0:
            return {"code": 400, "message": "数量和价格必须大于0"}
        engine = get_sync_engine()
        with engine.connect() as conn:
            result = conn.execute(text(f"SELECT quantity, cost_price FROM {TABLE_CURRENT_POSITIONS} WHERE code = :code"), {"code": code})
            holding = result.fetchone()
            if not holding:
                return {"code": 404, "message": "持仓不存在"}
            current_quantity = int(holding[0])
            current_cost = float(holding[1])
            if action == 'add':
                new_quantity = current_quantity + quantity
                new_cost = (current_cost * current_quantity + price * quantity) / new_quantity
                new_cost_price = round(new_cost, 2)
            elif action == 'reduce':
                if quantity >= current_quantity:
                    return {"code": 400, "message": "减仓数量不能大于或等于持仓数量"}
                new_quantity = current_quantity - quantity
                new_cost_price = current_cost
            else:
                return {"code": 400, "message": "无效的操作类型"}
            current_price = price
            try:
                up_ranks = await fetch_stock_rank('0')
                down_ranks = await fetch_stock_rank('1')
                all_stocks = {s['stock_code']: s for s in up_ranks + down_ranks}
                if code in all_stocks:
                    current_price = all_stocks[code].get('price', current_price)
            except:
                pass
            market_value = current_price * new_quantity
            total_cost = new_cost_price * new_quantity
            pnl = market_value - total_cost
            pnl_pct = round((pnl / total_cost) * 100, 2) if total_cost > 0 else 0
            conn.execute(
                text(f"""
                    UPDATE {TABLE_CURRENT_POSITIONS} 
                    SET quantity = :quantity, cost_price = :cost_price, market_price = :current_price,
                        market_value = :market_value, pnl = :pnl, pnl_pct = :pnl_pct, updated_at = NOW()
                    WHERE code = :code
                """),
                {
                    "quantity": new_quantity, 
                    "cost_price": new_cost_price,
                    "current_price": round(current_price, 2),
                    "market_value": round(market_value, 2),
                    "pnl": round(pnl, 2),
                    "pnl_pct": pnl_pct,
                    "code": code
                }
            )
            conn.commit()
        return {"code": 200, "message": f"{'加仓' if action == 'add' else '减仓'}成功"}
    except Exception as e:
        logger.error(f"更新持仓错误: {e}")
        return {"code": 500, "message": str(e)}

@app.post("/api/holdings/refresh")
async def refresh_holdings_prices(user_id: str):
    # 此函数保留但暂不使用 user_id（全局共享）
    try:
        engine = get_sync_engine()
        with engine.connect() as conn:
            holdings = conn.execute(
                text(f"SELECT code, quantity, cost_price FROM {TABLE_CURRENT_POSITIONS}")
            ).fetchall()
        if not holdings:
            return {"code": 200, "message": "无持仓数据"}
        up_ranks = await fetch_stock_rank('0')
        down_ranks = await fetch_stock_rank('1')
        all_stocks = {s['stock_code']: s for s in up_ranks + down_ranks}
        with engine.connect() as conn:
            for holding in holdings:
                code = holding[0]
                quantity = int(holding[1])
                cost_price = float(holding[2])
                quote = all_stocks.get(code, {})
                current_price = quote.get('price', cost_price)
                market_value = current_price * quantity
                total_cost = cost_price * quantity
                pnl = market_value - total_cost
                pnl_pct = round((pnl / total_cost) * 100, 2) if total_cost > 0 else 0
                conn.execute(
                    text(f"""
                        UPDATE {TABLE_CURRENT_POSITIONS} 
                        SET market_price = :current_price, market_value = :market_value,
                            pnl = :pnl, pnl_pct = :pnl_pct, updated_at = NOW()
                        WHERE code = :code
                    """),
                    {
                        "current_price": round(current_price, 2),
                        "market_value": round(market_value, 2),
                        "pnl": round(pnl, 2),
                        "pnl_pct": pnl_pct,
                        "code": code
                    }
                )
            conn.commit()
        return {"code": 200, "message": "价格刷新成功"}
    except Exception as e:
        logger.error(f"刷新持仓价格错误: {e}")
        return {"code": 500, "message": str(e)}

@app.get("/api/stock/picks/history")
async def get_stock_picks_history(date: str = Query(..., description="日期 YYYY-MM-DD")):
    try:
        engine = get_sync_engine()
        with engine.connect() as conn:
            result = conn.execute(
                text(f"""
                    SELECT code, name, price, change_pct, total_score, advice, fin_rating, date
                    FROM {TABLE_SELECTED}
                    WHERE date = :date
                    ORDER BY total_score DESC
                """),
                {"date": date}
            )
            rows = result.fetchall()
        if not rows:
            return {"code": 200, "data": [], "has_data": False, "msg": f"{date} 无选股数据"}
        picks = []
        for row in rows:
            picks.append({
                "code": row[0],
                "name": row[1],
                "price": float(row[2]) if row[2] else 0,
                "change_pct": float(row[3]) if row[3] else 0,
                "total_score": float(row[4]) if row[4] else 0,
                "advice": row[5] or '',
                "fin_rating": row[6] or '',
                "date": str(row[7]) if row[7] else ''
            })
        return {"code": 200, "data": picks, "has_data": True}
    except Exception as e:
        logger.error(f"获取历史选股结果错误: {e}")
        return {"code": 500, "message": str(e), "data": []}

# ========== 研报模块 ==========
def get_stock_report(page: int = 1, page_size: int = 20):
    results = []
    headers = {
        'Referer': 'https://data.eastmoney.com/report/stock.jshtml',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    json_data = {
        'beginTime': start_date,
        'endTime': end_date,
        'industryCode': '*',
        'ratingChange': None,
        'rating': None,
        'orgCode': None,
        'code': '*',
        'rcode': '',
        'pageSize': page_size,
        'pageIndex': page,
    }
    try:
        response = requests.post('https://reportapi.eastmoney.com/report/list2', headers=headers, json=json_data, timeout=15)
        data = response.json().get('data', [])
        for item in data:
            results.append({
                "id": item.get('reportId', ''),
                "stock_code": item.get('stockCode', ''),
                "stock_name": item.get('stockName', ''),
                "title": item.get('title', ''),
                "rating": item.get('emRatingName', '关注'),
                "industry": item.get('indvInduName', ''),
                "publish_date": item.get('publishDate', ''),
                "summary": item.get('title', '')[:100] + '...' if len(item.get('title', '')) > 100 else item.get('title', '')
            })
        return results
    except Exception as e:
        logger.error(f"获取研报错误: {e}")
        return []

@app.get("/api/research/jiufang")
async def get_jiufang_research(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    try:
        reports = get_stock_report(page, page_size)
        if reports:
            return {"code": 200, "data": reports, "total": len(reports), "page": page, "page_size": page_size, "message": "success"}
        else:
            return {"code": 200, "data": [], "total": 0, "message": "暂无研报数据"}
    except Exception as e:
        logger.error(f"获取研报异常: {e}")
        return {"code": 500, "message": str(e), "data": []}

@app.get("/api/research/stock")
async def get_stock_research(stock_code: str = Query(...), limit: int = Query(20, ge=1, le=50)):
    try:
        all_reports = []
        for page in range(1, 4):
            reports = get_stock_report(page, 30)
            if not reports:
                break
            all_reports.extend(reports)
            time.sleep(0.3)
        filtered = [r for r in all_reports if r['stock_code'] == stock_code][:limit]
        return {"code": 200, "data": filtered, "message": "success"}
    except Exception as e:
        logger.error(f"获取个股研报错误: {e}")
        return {"code": 500, "message": str(e), "data": []}

@app.get("/api/research/latest")
async def get_latest_research(limit: int = Query(20, ge=1, le=50)):
    try:
        reports = get_stock_report(1, limit)
        return {"code": 200, "data": reports, "message": "success"}
    except Exception as e:
        logger.error(f"获取最新研报错误: {e}")
        return {"code": 500, "message": str(e), "data": []}

@app.post("/api/user/login")
async def user_login(request: Request):
    try:
        body = await request.json()
        code = body.get('code')
        user_id = body.get('user_id')
        return {
            "code": 200,
            "data": {
                "id": user_id or "stock_user",
                "name": "股票用户",
                "role": "user",
                "openid": code or user_id or "mock_openid"
            }
        }
    except Exception as e:
        return {"code": 500, "message": str(e)}

# ========== 选股执行模块（新增） ==========
# 选股任务状态存储
pick_tasks = {}
pick_lock = threading.Lock()

def _get_pick_script_path() -> str:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    possible_paths = [
        os.path.join(current_dir, "daily_pick_stocks.py"),
        os.path.join(current_dir, "stock_money", "daily_pick_stocks.py"),
        os.path.join(os.path.dirname(current_dir), "stock_money", "daily_pick_stocks.py"),
    ]
    for path in possible_paths:
        if os.path.exists(path):
            return path
    raise FileNotFoundError("找不到 daily_pick_stocks.py 脚本")

def _run_pick_task(task_id: str):
    try:
        with pick_lock:
            pick_tasks[task_id]['status'] = 'running'
            pick_tasks[task_id]['started_at'] = datetime.now().isoformat()
            pick_tasks[task_id]['message'] = '正在加载数据...'
            pick_tasks[task_id]['progress'] = 10
        script_path = _get_pick_script_path()
        script_dir = os.path.dirname(script_path)
        with pick_lock:
            pick_tasks[task_id]['message'] = '执行选股中...'
            pick_tasks[task_id]['progress'] = 30
        process = subprocess.Popen(
            [sys.executable, script_path],
            cwd=script_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8'
        )
        stdout_lines = []
        stderr_lines = []
        def read_stdout():
            for line in iter(process.stdout.readline, ''):
                if line:
                    stdout_lines.append(line)
                    if '进度' in line or '%' in line:
                        with pick_lock:
                            pick_tasks[task_id]['message'] = line.strip()
                            import re
                            match = re.search(r'(\d+)%', line)
                            if match:
                                pick_tasks[task_id]['progress'] = min(90, int(match.group(1)))
                    if '选股完成' in line or 'selected_stocks' in line:
                        with pick_lock:
                            pick_tasks[task_id]['progress'] = 90
                    logger.info(f"[选股] {line.strip()}")
        def read_stderr():
            for line in iter(process.stderr.readline, ''):
                if line:
                    stderr_lines.append(line)
                    logger.warning(f"[选股错误] {line.strip()}")
        stdout_thread = threading.Thread(target=read_stdout)
        stderr_thread = threading.Thread(target=read_stderr)
        stdout_thread.start()
        stderr_thread.start()
        process.wait()
        stdout_thread.join()
        stderr_thread.join()
        if process.returncode == 0:
            with pick_lock:
                pick_tasks[task_id]['status'] = 'completed'
                pick_tasks[task_id]['message'] = '选股完成'
                pick_tasks[task_id]['completed_at'] = datetime.now().isoformat()
                pick_tasks[task_id]['progress'] = 100
                pick_tasks[task_id]['output'] = ''.join(stdout_lines[-3000:]) if stdout_lines else ''
            logger.info(f"选股任务 {task_id} 完成")
        else:
            error_msg = ''.join(stderr_lines[-10:]) if stderr_lines else '未知错误'
            with pick_lock:
                pick_tasks[task_id]['status'] = 'failed'
                pick_tasks[task_id]['message'] = f'选股失败: {error_msg[:200]}'
                pick_tasks[task_id]['error'] = error_msg
                pick_tasks[task_id]['progress'] = 0
            logger.error(f"选股任务 {task_id} 失败: {error_msg}")
    except Exception as e:
        logger.error(f"选股任务 {task_id} 异常: {str(e)}")
        import traceback
        with pick_lock:
            pick_tasks[task_id]['status'] = 'failed'
            pick_tasks[task_id]['message'] = f'选股异常: {str(e)}'
            pick_tasks[task_id]['error'] = traceback.format_exc()
            pick_tasks[task_id]['progress'] = 0

@app.post("/api/stock/run-pick")
async def run_stock_pick():
    try:
        with pick_lock:
            for tid, task in pick_tasks.items():
                if task.get('status') == 'running':
                    return {"code": 400, "message": "已有选股任务正在运行，请稍后", "data": {"task_id": tid, "status": "running"}}
        try:
            script_path = _get_pick_script_path()
            logger.info(f"选股脚本路径: {script_path}")
        except FileNotFoundError as e:
            return {"code": 500, "message": f"选股脚本不存在: {str(e)}"}
        task_id = str(uuid.uuid4())
        with pick_lock:
            pick_tasks[task_id] = {
                'task_id': task_id,
                'status': 'pending',
                'message': '任务已提交，等待执行...',
                'progress': 0,
                'started_at': None,
                'completed_at': None,
                'error': None,
                'output': None,
            }
        thread = threading.Thread(target=_run_pick_task, args=(task_id,))
        thread.daemon = True
        thread.start()
        logger.info(f"选股任务已提交: {task_id}")
        return {"code": 200, "message": "选股任务已提交", "data": {"task_id": task_id, "status": "pending"}}
    except Exception as e:
        logger.error(f"提交选股任务失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return {"code": 500, "message": f"提交失败: {str(e)}"}

@app.get("/api/stock/pick-status")
async def get_pick_status(task_id: str = Query(..., description="任务ID")):
    try:
        with pick_lock:
            if task_id not in pick_tasks:
                return {"code": 404, "message": "任务不存在"}
            task = pick_tasks[task_id].copy()
        if task.get('status') == 'completed':
            try:
                from db_manager import get_latest_picks
                latest_picks = get_latest_picks()
                if latest_picks:
                    task['latest_picks'] = latest_picks
            except Exception as e:
                logger.warning(f"获取最新选股结果失败: {e}")
        return {"code": 200, "data": task}
    except Exception as e:
        logger.error(f"查询任务状态失败: {str(e)}")
        return {"code": 500, "message": str(e)}

@app.get("/api/stock/pick-tasks")
async def get_pick_tasks(limit: int = Query(10, ge=1, le=50)):
    try:
        with pick_lock:
            tasks = []
            for tid, task in pick_tasks.items():
                task_copy = task.copy()
                task_copy['task_id'] = tid
                tasks.append(task_copy)
            tasks.sort(key=lambda x: x.get('started_at') or x.get('completed_at') or '', reverse=True)
            tasks = tasks[:limit]
        return {"code": 200, "data": tasks, "total": len(tasks)}
    except Exception as e:
        logger.error(f"获取任务列表失败: {str(e)}")
        return {"code": 500, "message": str(e)}

@app.get("/api/stock/latest-picks")
async def get_latest_picks_api():
    try:
        from db_manager import get_latest_picks
        picks = get_latest_picks()
        if picks:
            return {"code": 200, "data": picks, "has_data": True}
        else:
            return {"code": 200, "data": [], "has_data": False, "message": "暂无选股数据"}
    except ImportError as e:
        logger.warning(f"db_manager 导入失败: {e}")
        try:
            import pandas as pd
            from config import OUTPUT_DIR
            latest_file = None
            for f in os.listdir(OUTPUT_DIR):
                if f.startswith('selected_stocks_') and f.endswith('.csv'):
                    latest_file = f
                    break
            if latest_file:
                df = pd.read_csv(os.path.join(OUTPUT_DIR, latest_file))
                picks = df.to_dict('records')
                return {"code": 200, "data": picks, "has_data": True}
        except Exception as e2:
            logger.warning(f"从文件读取选股结果失败: {e2}")
        return {"code": 200, "data": [], "has_data": False, "message": "暂无选股数据"}
    except Exception as e:
        logger.error(f"获取最新选股结果失败: {e}")
        return {"code": 500, "message": str(e), "data": []}

# ========== 启动事件 ==========
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Stock Watch System...")
    await init_database()
    logger.info("Stock Watch System ready!")
    yield
    if db_pool:
        await db_pool.close()

app.router.lifespan_context = lifespan

# ========== 主入口 ==========
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8081))
    uvicorn.run(app, host="0.0.0.0", port=port)
