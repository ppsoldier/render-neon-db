import os
import asyncio
import sys
import uuid
import threading
import subprocess
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import asyncpg
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
# 添加 APScheduler 导入
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import atexit



# ---- 新增 Redis 导入 ----
import redis

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
    """获取选股结果（如果当天没有，自动获取最近一天）"""
    try:
        engine = get_sync_engine()
        today = datetime.now().strftime('%Y-%m-%d')
        
        with engine.connect() as conn:
            # 1. 检查当天是否有数据
            result = conn.execute(
                text(f"SELECT COUNT(*) FROM {TABLE_SELECTED} WHERE date = :date"),
                {"date": today}
            )
            count = result.fetchone()[0]
            
            data_date = today
            is_fallback = False
            
            # 2. 如果当天没有数据，获取最近一天
            if count == 0:
                result = conn.execute(
                    text(f"SELECT DISTINCT date FROM {TABLE_SELECTED} ORDER BY date DESC LIMIT 1")
                )
                row = result.fetchone()
                if not row:
                    return {
                        "code": 200,
                        "data": [],
                        "has_data": False,
                        "msg": "暂无选股数据",
                        "data_date": None,
                        "is_fallback": False
                    }
                data_date = str(row[0])
                is_fallback = True
                logger.info(f"当天无选股数据，使用 {data_date} 的数据")
            
            # 3. 查询数据
            result = conn.execute(
                text(f"""
                    SELECT code, name, price, change_pct, total_score, advice, fin_rating, date
                    FROM {TABLE_SELECTED}
                    WHERE date = :date
                    ORDER BY total_score DESC
                """),
                {"date": data_date}
            )
            rows = result.fetchall()
        
        if not rows:
            return {
                "code": 200,
                "data": [],
                "has_data": False,
                "msg": f"{data_date} 无选股数据",
                "data_date": data_date,
                "is_fallback": is_fallback
            }
        
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
        
        return {
            "code": 200,
            "data": picks,
            "has_data": True,
            "data_date": data_date,
            "is_fallback": is_fallback
        }
        
    except Exception as e:
        logger.error(f"获取选股结果错误: {e}")
        return {"code": 500, "message": str(e), "data": []}
        

@app.get("/api/stock/market")
async def get_market_data():
    """获取市场分析数据（如果当天没有，自动获取最近一天）"""
    try:
        engine = get_sync_engine()
        today = datetime.now().strftime('%Y-%m-%d')
        
        with engine.connect() as conn:
            # 1. 检查当天是否有数据
            result = conn.execute(
                text(f"SELECT COUNT(*) FROM {TABLE_MARKET} WHERE date = :date"),
                {"date": today}
            )
            count = result.fetchone()[0]
            
            data_date = today
            is_fallback = False
            
            # 2. 如果当天没有数据，获取最近一天
            if count == 0:
                result = conn.execute(
                    text(f"SELECT DISTINCT date FROM {TABLE_MARKET} ORDER BY date DESC LIMIT 1")
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
                        "data_date": None,
                        "is_fallback": False,
                        "msg": "暂无市场数据"
                    }
                data_date = str(row[0])
                is_fallback = True
                logger.info(f"当天无市场数据，使用 {data_date} 的数据")
            
            # 3. 查询数据
            result = conn.execute(
                text(f"""
                    SELECT market_state, market_score, position_ratio, advice, 
                           trend_strength, volatility, ma_arrangement,
                           limit_up_count, limit_down_count, up_count, down_count, advance_percent
                    FROM {TABLE_MARKET}
                    WHERE date = :date
                """),
                {"date": data_date}
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
                "data_date": data_date,
                "is_fallback": is_fallback,
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
        
        return {
            "code": 200,
            "data": market_data,
            "data_date": data_date,
            "is_fallback": is_fallback
        }
        
    except Exception as e:
        logger.error(f"获取市场数据错误: {e}")
        return {"code": 500, "message": str(e), "data": None}


@app.get("/api/stock/sentiment")
async def get_sentiment_data():
    """获取市场情绪数据（如果当天没有，自动获取最近一天）"""
    try:
        engine = get_sync_engine()
        today = datetime.now().strftime('%Y-%m-%d')
        
        with engine.connect() as conn:
            # 1. 检查当天是否有概念数据
            result = conn.execute(
                text(f"SELECT COUNT(*) FROM {TABLE_CONCEPTS} WHERE date = :date"),
                {"date": today}
            )
            count = result.fetchone()[0]
            
            data_date = today
            is_fallback = False
            
            # 2. 如果当天没有数据，获取最近一天
            if count == 0:
                result = conn.execute(
                    text(f"SELECT DISTINCT date FROM {TABLE_CONCEPTS} ORDER BY date DESC LIMIT 1")
                )
                row = result.fetchone()
                if not row:
                    return {
                        "code": 200,
                        "data": {"concepts": [], "industries": []},
                        "data_date": None,
                        "is_fallback": False
                    }
                data_date = str(row[0])
                is_fallback = True
                logger.info(f"当天无情绪数据，使用 {data_date} 的数据")
            
            # 3. 获取热点概念
            concepts_result = conn.execute(
                text(f"""
                    SELECT concept_name, change_pct, leading_stock
                    FROM {TABLE_CONCEPTS}
                    WHERE date = :date
                    ORDER BY change_pct DESC
                    LIMIT 5
                """),
                {"date": data_date}
            )
            concepts = []
            for row in concepts_result.fetchall():
                concepts.append({
                    "name": row[0],
                    "change_pct": float(row[1]) if row[1] else 0,
                    "leading_stock": row[2] or ''
                })
            
            # 4. 获取热点行业
            industries_result = conn.execute(
                text(f"""
                    SELECT industry_name, change_pct, leading_stock
                    FROM {TABLE_INDUSTRIES}
                    WHERE date = :date
                    ORDER BY change_pct DESC
                    LIMIT 5
                """),
                {"date": data_date}
            )
            industries = []
            for row in industries_result.fetchall():
                industries.append({
                    "name": row[0],
                    "change_pct": float(row[1]) if row[1] else 0,
                    "leading_stock": row[2] or ''
                })
        
        return {
            "code": 200,
            "data": {"concepts": concepts, "industries": industries},
            "data_date": data_date,
            "is_fallback": is_fallback
        }
        
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
            # 1. 获取持仓明细
            rows = await conn.fetch("""
                SELECT code, name, quantity, cost_price, market_price
                FROM stock_data.current_positions
                ORDER BY code
            """)
            
            # 2. 获取账户总览
            account = await conn.fetchrow("""
                SELECT snapshot_date, initial_capital, cash, total_value, total_pnl, total_pnl_pct
                FROM stock_data.account_state
                ORDER BY snapshot_date DESC
                LIMIT 1
            """)
        
        # 获取实时行情
        holdings = []
        codes = [row['code'] for row in rows]
        quotes = fetch_realtime_quotes(codes)
        
        total_market_value = 0
        total_cost = 0
        total_pnl = 0
        today_pnl = 0  # 今日盈亏
        prev_close = 0  # 昨日收盘价
        
        for row in rows:
            code = row['code']
            quote = quotes.get(code, {})
            current_price = quote.get('price', 0) or float(row['market_price']) or float(row['cost_price'])
            
            # 获取昨日收盘价（从新浪或其他数据源）
            # 这里简化处理，实际可以从数据库读取昨日数据
            yesterday_close = current_price * 0.98  # 示例，实际需要真实数据
            
            quantity = float(row['quantity'])
            cost_price = float(row['cost_price'])
            
            market_value = current_price * quantity
            cost_sum = cost_price * quantity
            pnl = market_value - cost_sum
            
            # 计算今日盈亏 = (当前价 - 昨日收盘价) * 数量
            today_pnl += (current_price - yesterday_close) * quantity
            
            total_market_value += market_value
            total_cost += cost_sum
            total_pnl += pnl
            
            holdings.append({
                "code": code,
                "name": row['name'],
                "quantity": quantity,
                "cost_price": round(cost_price, 2),
                "current_price": round(current_price, 2),
                "market_value": round(market_value, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round((pnl / cost_sum) * 100, 2) if cost_sum > 0 else 0
            })
        
        # 构建返回数据
        if account:
            account_data = {
                "snapshot_date": str(account['snapshot_date']),
                "initial_capital": float(account['initial_capital']),
                "cash": float(account['cash']),
                "total_value": float(account['total_value']),
                "total_pnl": float(account['total_pnl']),
                "total_pnl_pct": float(account['total_pnl_pct']),
                "today_pnl": round(today_pnl, 2),  # 今日盈亏
                "today_pnl_pct": round((today_pnl / float(account['initial_capital'])) * 100, 2) if account['initial_capital'] > 0 else 0
            }
        else:
            account_data = {
                "snapshot_date": datetime.now().strftime("%Y-%m-%d"),
                "initial_capital": 100000,
                "cash": 100000 - total_cost,
                "total_value": round(total_market_value + (100000 - total_cost), 2),
                "total_pnl": round(total_pnl, 2),
                "total_pnl_pct": round((total_pnl / 100000) * 100, 2) if 100000 > 0 else 0,
                "today_pnl": round(today_pnl, 2),
                "today_pnl_pct": round((today_pnl / 100000) * 100, 2) if 100000 > 0 else 0
            }
        
        return {
            "code": 200,
            "data": holdings,
            "account": account_data
        }
    except Exception as e:
        logger.error(f"持仓接口错误: {e}")
        return {"code": 500, "message": str(e), "data": [], "account": {}}
        

@app.post("/api/holdings")
async def add_holding(request: Request):
    """添加持仓（实时计算盈亏）"""
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
        
        # 获取实时行情
        current_price = cost_price
        try:
            up_ranks = await fetch_stock_rank('0')
            down_ranks = await fetch_stock_rank('1')
            all_stocks = {s['stock_code']: s for s in up_ranks + down_ranks}
            if code in all_stocks:
                current_price = all_stocks[code].get('price', cost_price)
            else:
                # 如果九方智投没有，尝试新浪
                quotes = fetch_realtime_quotes([code])
                if code in quotes:
                    current_price = quotes[code].get('price', cost_price)
        except:
            pass
        
        # 实时计算盈亏
        market_value = current_price * quantity
        total_cost = cost_price * quantity
        pnl = market_value - total_cost
        pnl_pct = round((pnl / total_cost) * 100, 2) if total_cost > 0 else 0
        
        with engine.connect() as conn:
            # 检查是否已存在
            existing = conn.execute(
                text(f"SELECT code FROM {TABLE_CURRENT_POSITIONS} WHERE code = :code"),
                {"code": code}
            ).fetchone()
            
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
                    "quantity": quantity, "cost_price": cost_price, 
                    "current_price": round(current_price, 2),
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
    """更新持仓（加仓/减仓）并实时计算盈亏"""
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
            # 获取当前持仓
            result = conn.execute(
                text(f"SELECT quantity, cost_price FROM {TABLE_CURRENT_POSITIONS} WHERE code = :code"),
                {"code": code}
            )
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
            
            # 获取实时行情
            current_price = price
            try:
                up_ranks = await fetch_stock_rank('0')
                down_ranks = await fetch_stock_rank('1')
                all_stocks = {s['stock_code']: s for s in up_ranks + down_ranks}
                if code in all_stocks:
                    current_price = all_stocks[code].get('price', current_price)
                else:
                    quotes = fetch_realtime_quotes([code])
                    if code in quotes:
                        current_price = quotes[code].get('price', current_price)
            except:
                pass
            
            # 实时计算盈亏
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
    """获取指定日期的历史选股结果（如果当天没有，自动获取最近一天）"""
    try:
        engine = get_sync_engine()
        
        with engine.connect() as conn:
            # 1. 检查指定日期是否有数据
            result = conn.execute(
                text(f"SELECT COUNT(*) FROM {TABLE_SELECTED} WHERE date = :date"),
                {"date": date}
            )
            count = result.fetchone()[0]
            
            query_date = date
            is_fallback = False
            
            # 2. 如果指定日期没有数据，获取该日期之前最近一天有数据的日期
            if count == 0:
                result = conn.execute(
                    text(f"SELECT DISTINCT date FROM {TABLE_SELECTED} WHERE date <= :date ORDER BY date DESC LIMIT 1"),
                    {"date": date}
                )
                row = result.fetchone()
                if not row:
                    return {
                        "code": 200,
                        "data": [],
                        "has_data": False,
                        "msg": f"{date} 及之前无选股数据",
                        "data_date": date,
                        "is_fallback": False
                    }
                query_date = str(row[0])
                is_fallback = True
                logger.info(f"{date} 无选股数据，使用 {query_date} 的数据")
            
            # 3. 查询数据
            result = conn.execute(
                text(f"""
                    SELECT code, name, price, change_pct, total_score, advice, fin_rating, date
                    FROM {TABLE_SELECTED}
                    WHERE date = :date
                    ORDER BY total_score DESC
                """),
                {"date": query_date}
            )
            rows = result.fetchall()
        
        if not rows:
            return {
                "code": 200,
                "data": [],
                "has_data": False,
                "msg": f"{query_date} 无选股数据",
                "data_date": query_date,
                "is_fallback": is_fallback
            }
        
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
        
        return {
            "code": 200,
            "data": picks,
            "has_data": True,
            "data_date": query_date,
            "is_fallback": is_fallback
        }
        
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

# ========== 选股执行模块（Redis 版本） ==========
# 初始化 Redis 连接
REDIS_URL = os.environ.get("REDIS_URL", "redis://default:FBRTgBVjJPiTrVTBCpaZqrSfVsaIaxrA@redis.railway.internal:6379")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

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

def _save_task_to_redis(task_id: str, task_data: dict):
    """将任务数据存入 Redis，1小时过期"""
    key = f"task:{task_id}"
    redis_client.setex(key, 3600, json.dumps(task_data))

def _get_task_from_redis(task_id: str) -> Optional[dict]:
    key = f"task:{task_id}"
    data = redis_client.get(key)
    if data:
        return json.loads(data)
    return None

def _update_task_in_redis(task_id: str, updates: dict):
    """更新 Redis 中的任务状态（部分字段）"""
    task = _get_task_from_redis(task_id)
    if task:
        task.update(updates)
        _save_task_to_redis(task_id, task)

def _run_pick_task(task_id: str):
    try:
        # 更新状态为 running
        _update_task_in_redis(task_id, {
            'status': 'running',
            'started_at': datetime.now().isoformat(),
            'message': '正在加载数据...',
            'progress': 10
        })

        script_path = _get_pick_script_path()
        script_dir = os.path.dirname(script_path)
        _update_task_in_redis(task_id, {'message': '执行选股中...', 'progress': 30})

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
                    # 解析进度信息
                    if '进度' in line or '%' in line:
                        msg = line.strip()
                        import re
                        match = re.search(r'(\d+)%', line)
                        progress = min(90, int(match.group(1))) if match else 50
                        _update_task_in_redis(task_id, {'message': msg, 'progress': progress})
                    if '选股完成' in line or 'selected_stocks' in line:
                        _update_task_in_redis(task_id, {'progress': 90})
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
            _update_task_in_redis(task_id, {
                'status': 'completed',
                'message': '选股完成',
                'completed_at': datetime.now().isoformat(),
                'progress': 100,
                'output': ''.join(stdout_lines[-3000:]) if stdout_lines else ''
            })
            logger.info(f"选股任务 {task_id} 完成")
        else:
            error_msg = ''.join(stderr_lines[-10:]) if stderr_lines else '未知错误'
            _update_task_in_redis(task_id, {
                'status': 'failed',
                'message': f'选股失败: {error_msg[:200]}',
                'error': error_msg,
                'progress': 0
            })
            logger.error(f"选股任务 {task_id} 失败: {error_msg}")
    except Exception as e:
        logger.error(f"选股任务 {task_id} 异常: {str(e)}")
        import traceback
        _update_task_in_redis(task_id, {
            'status': 'failed',
            'message': f'选股异常: {str(e)}',
            'error': traceback.format_exc(),
            'progress': 0
        })

@app.post("/api/stock/run-pick")
async def run_stock_pick():
    try:
        # 检查是否有正在运行的任务（可选，简单起见可跳过）
        task_id = str(uuid.uuid4())
        initial_data = {
            'task_id': task_id,
            'status': 'pending',
            'message': '任务已提交，等待执行...',
            'progress': 0,
            'started_at': None,
            'completed_at': None,
            'error': None,
            'output': None,
        }
        _save_task_to_redis(task_id, initial_data)

        # 启动后台线程执行选股
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
        task = _get_task_from_redis(task_id)
        if not task:
            return {"code": 404, "message": "任务不存在"}
        # 如果任务已完成，尝试从数据库获取最新选股结果
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
    # 注意：此接口无法直接列出所有任务（Redis 不支持按模式查询所有键）
    # 可通过 SCAN 命令实现，但此处简化，返回一个提示
    return {"code": 200, "data": [], "message": "请使用任务ID单独查询状态"}

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




# ========== 自动交易模块 ==========
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import uuid
import json

# ========== 模拟账户类（数据库存储）==========
class SimulatedAccountDB:
    """模拟交易账户 - 数据直接读写数据库"""
    
    def __init__(self):
        self.engine = get_sync_engine()
        self.commission_rate = 0.0001
        self.min_commission = 5.0
        self.stamp_tax_rate = 0.001
        self._config = None
        self._load_config()
        self._init_account()


    def _load_config(self):
        """加载止盈止损配置"""
        try:
            from db_manager import get_stop_loss_config
            self._config = get_stop_loss_config()
            if self._config:
                logger.info(f"加载配置: 止损={self._config['stop_loss_pct']}%, 止盈={self._config['take_profit_pct']}%")
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            # 默认值
            self._config = {
                'stop_loss_pct': -7.0,
                'take_profit_pct': 15.0,
                'max_daily_trades': 20,
                'single_buy_amount': 10000,
                'min_buy_score': 50
            }
    
    def check_stop_conditions(self):
        """检查所有持仓的止盈止损（使用配置）"""
        results = []
        positions = self._get_all_positions()
        
        stop_loss_pct = self._config.get('stop_loss_pct', -7.0)
        take_profit_pct = self._config.get('take_profit_pct', 15.0)
        
        for pos in positions:
            current_price = pos['market_price']
            cost_price = pos['cost_price']
            pnl_pct = (current_price - cost_price) / cost_price * 100 if cost_price > 0 else 0
            
            if pnl_pct <= stop_loss_pct:
                success, msg = self.sell(pos['code'], current_price, pos['quantity'], 
                                         f"止损触发: {pnl_pct:.2f}% (阈值{stop_loss_pct}%)")
                results.append({'code': pos['code'], 'action': 'stop_loss', 'success': success, 'message': msg})
            elif pnl_pct >= take_profit_pct:
                success, msg = self.sell(pos['code'], current_price, pos['quantity'], 
                                         f"止盈触发: {pnl_pct:.2f}% (阈值{take_profit_pct}%)")
                results.append({'code': pos['code'], 'action': 'take_profit', 'success': success, 'message': msg})
        
        return results
    
    
    
    
    def _init_account(self):
        """初始化或加载账户状态"""
        with self.engine.connect() as conn:
            # 检查 account_state 表是否有数据
            result = conn.execute(
                text(f"SELECT COUNT(*) FROM {SCHEMA_NAME}.account_state")
            )
            count = result.fetchone()[0]
            
            if count == 0:
                conn.execute(
                    text(f"""
                        INSERT INTO {SCHEMA_NAME}.account_state 
                        (snapshot_date, initial_capital, cash, total_value, total_pnl, total_pnl_pct)
                        VALUES (CURRENT_DATE, 100000, 100000, 100000, 0, 0)
                    """)
                )
                conn.commit()


    
    def _get_account_state(self):
        """获取最新账户状态"""
        try:
            with self.engine.connect() as conn:
                # 获取最新的一条记录
                result = conn.execute(
                    text(f"""
                        SELECT initial_capital, cash, total_value, total_pnl, total_pnl_pct, snapshot_date
                        FROM {SCHEMA_NAME}.account_state
                        ORDER BY id DESC
                        LIMIT 1
                    """)
                )
                row = result.fetchone()
                if row:
                    return {
                        'initial_capital': float(row[0]),
                        'cash': float(row[1]),
                        'total_value': float(row[2]),
                        'total_pnl': float(row[3]),
                        'total_pnl_pct': float(row[4]),
                        'snapshot_date': str(row[5]) if row[5] else None
                    }
                return None
        except Exception as e:
            logger.error(f"获取账户状态失败: {e}")
            return None

    
    
    def _update_account_state(self, cash, total_value, total_pnl, total_pnl_pct):
        """更新账户状态"""
        with self.engine.connect() as conn:
            conn.execute(
                text(f"""
                    INSERT INTO {SCHEMA_NAME}.account_state 
                    (snapshot_date, initial_capital, cash, total_value, total_pnl, total_pnl_pct)
                    VALUES (CURRENT_DATE, 100000, :cash, :total_value, :total_pnl, :total_pnl_pct)
                """),
                {
                    'cash': cash,
                    'total_value': total_value,
                    'total_pnl': total_pnl,        # 传入正确的总盈亏
                    'total_pnl_pct': total_pnl_pct # 传入正确的总收益率
                }
            )
            conn.commit()
    
    def _get_position(self, code):
        """获取单个持仓"""
        with self.engine.connect() as conn:
            result = conn.execute(
                text(f"""
                    SELECT code, name, quantity, cost_price, market_price, market_value, pnl, pnl_pct
                    FROM {SCHEMA_NAME}.current_positions
                    WHERE code = :code
                """),
                {'code': code}
            )
            row = result.fetchone()
            if row:
                return {
                    'code': row[0],
                    'name': row[1],
                    'quantity': float(row[2]),
                    'cost_price': float(row[3]),
                    'market_price': float(row[4]) if row[4] else float(row[3]),
                    'market_value': float(row[5]) if row[5] else 0,
                    'pnl': float(row[6]) if row[6] else 0,
                    'pnl_pct': float(row[7]) if row[7] else 0
                }
            return None
    
    def _get_all_positions(self):
        """获取所有持仓"""
        with self.engine.connect() as conn:
            result = conn.execute(
                text(f"""
                    SELECT code, name, quantity, cost_price, market_price, market_value, pnl, pnl_pct
                    FROM {SCHEMA_NAME}.current_positions
                    ORDER BY code
                """)
            )
            rows = result.fetchall()
            positions = []
            for row in rows:
                positions.append({
                    'code': row[0],
                    'name': row[1],
                    'quantity': float(row[2]),
                    'cost_price': float(row[3]),
                    'market_price': float(row[4]) if row[4] else float(row[3]),
                    'market_value': float(row[5]) if row[5] else 0,
                    'pnl': float(row[6]) if row[6] else 0,
                    'pnl_pct': float(row[7]) if row[7] else 0
                })
            return positions
    
    def _update_position(self, code, name, quantity, cost_price, market_price, market_value, pnl, pnl_pct):
        """更新或插入持仓"""
        with self.engine.connect() as conn:
            existing = conn.execute(
                text(f"SELECT code FROM {SCHEMA_NAME}.current_positions WHERE code = :code"),
                {'code': code}
            ).fetchone()
            
            if existing:
                conn.execute(
                    text(f"""
                        UPDATE {SCHEMA_NAME}.current_positions 
                        SET name = :name, quantity = :quantity, cost_price = :cost_price,
                            market_price = :market_price, market_value = :market_value,
                            pnl = :pnl, pnl_pct = :pnl_pct, updated_at = NOW()
                        WHERE code = :code
                    """),
                    {
                        'code': code, 'name': name, 'quantity': quantity,
                        'cost_price': cost_price, 'market_price': market_price,
                        'market_value': market_value, 'pnl': pnl, 'pnl_pct': pnl_pct
                    }
                )
            else:
                conn.execute(
                    text(f"""
                        INSERT INTO {SCHEMA_NAME}.current_positions 
                        (code, name, quantity, cost_price, market_price, market_value, pnl, pnl_pct, created_at, updated_at)
                        VALUES (:code, :name, :quantity, :cost_price, :market_price, :market_value, :pnl, :pnl_pct, NOW(), NOW())
                    """),
                    {
                        'code': code, 'name': name, 'quantity': quantity,
                        'cost_price': cost_price, 'market_price': market_price,
                        'market_value': market_value, 'pnl': pnl, 'pnl_pct': pnl_pct
                    }
                )
            conn.commit()
    
    def _delete_position(self, code):
        """删除持仓"""
        with self.engine.connect() as conn:
            conn.execute(
                text(f"DELETE FROM {SCHEMA_NAME}.current_positions WHERE code = :code"),
                {'code': code}
            )
            conn.commit()
    
    def _calc_commission(self, amount):
        commission = amount * self.commission_rate
        return max(commission, self.min_commission)
    
    def _get_current_price(self, code):
        """获取实时价格"""
        try:
            quotes = fetch_realtime_quotes([code])
            if code in quotes:
                return quotes[code]['price']
            return None
        except Exception as e:
            logger.error(f"获取实时价格失败 {code}: {e}")
            return None
    
    def buy(self, code, name, price, quantity, reason=""):
        max_trades = self._config.get('max_daily_trades', 20)
        """买入/加仓"""
        if quantity <= 0:
            return False, "数量必须大于0"
        
        trade_amount = price * quantity
        commission = self._calc_commission(trade_amount)
        total_cost = trade_amount + commission
        
        account = self._get_account_state()
        if not account:
            return False, "账户状态异常"
        
        cash = account['cash']
        if cash < total_cost:
            return False, f"资金不足 (需要 {total_cost:.2f}，可用 {cash:.2f})"
        
        existing = self._get_position(code)
        
        if existing:
            old_qty = existing['quantity']
            old_cost = existing['cost_price']
            new_qty = old_qty + quantity
            new_cost = (old_cost * old_qty + total_cost) / new_qty
            
            self._update_position(
                code, name, new_qty, new_cost, price,
                price * new_qty,
                (price - new_cost) * new_qty,
                ((price - new_cost) / new_cost) * 100 if new_cost > 0 else 0
            )
            action = "加仓"
        else:
            self._update_position(
                code, name, quantity, price, price,
                price * quantity, 0, 0
            )
            action = "买入"
        
        new_cash = cash - total_cost

        # buy 方法中的修复
        positions = self._get_all_positions()
        total_market_value = sum([p['market_value'] for p in positions])
        total_cost = sum([p['cost_price'] * p['quantity'] for p in positions])
        
        # 正确计算总盈亏
        total_value = new_cash + total_market_value
        initial_capital = account['initial_capital']
        total_pnl = total_value - initial_capital
        total_pnl_pct = (total_pnl / initial_capital) * 100 if initial_capital > 0 else 0
        
        self._update_account_state(new_cash, total_value, total_pnl, total_pnl_pct)       
        
        # 记录交易
        self._save_trade_record(code, name, action, price, quantity, trade_amount, commission, 0, 0, 0, reason)
        
        log_msg = f"{action} - {name}({code}) 价格{price:.2f} x {quantity}股 = {trade_amount:.2f}元 原因: {reason}"
        return True, log_msg
    
    def sell(self, code, price, quantity, reason=""):
        """卖出/减仓"""
        existing = self._get_position(code)
        if not existing:
            return False, f"无持仓: {code}"
        
        if quantity <= 0 or quantity > existing['quantity']:
            return False, f"持仓不足 (持有 {existing['quantity']}股)"
        
        trade_amount = price * quantity
        commission = self._calc_commission(trade_amount)
        stamp_tax = trade_amount * self.stamp_tax_rate
        net_proceeds = trade_amount - commission - stamp_tax
        
        avg_cost = existing['cost_price']
        pnl = (price - avg_cost) * quantity - commission - stamp_tax
        pnl_pct = ((price - avg_cost) / avg_cost) * 100 if avg_cost > 0 else 0
        
        account = self._get_account_state()
        new_cash = account['cash'] + net_proceeds
        
        if quantity >= existing['quantity']:
            self._delete_position(code)
            action = "清仓"
        else:
            new_qty = existing['quantity'] - quantity
            self._update_position(
                code, existing['name'], new_qty, existing['cost_price'], price,
                price * new_qty,
                (price - existing['cost_price']) * new_qty,
                ((price - existing['cost_price']) / existing['cost_price']) * 100
            )
            action = "减仓"
        
        positions = self._get_all_positions()
        total_market_value = sum([p['market_value'] for p in positions])
        total_pnl = sum([p['pnl'] for p in positions])
        total_value = new_cash + total_market_value
        total_pnl_pct = (total_pnl / account['initial_capital']) * 100 if account['initial_capital'] > 0 else 0
        
        self._update_account_state(new_cash, total_value, total_pnl, total_pnl_pct)
        
        # 记录交易
        self._save_trade_record(code, existing['name'], action, price, quantity, trade_amount, commission, stamp_tax, pnl, pnl_pct, reason)
        
        log_msg = f"{action} - {existing['name']}({code}) 价格{price:.2f} x {quantity}股 盈亏: {pnl:+.2f} ({pnl_pct:+.2f}%) 原因: {reason}"
        return True, log_msg
    
    def _save_trade_record(self, code, name, action, price, quantity, amount, commission, stamp_tax, pnl, pnl_pct, reason):
        """保存交易记录到数据库"""
        try:
            with self.engine.connect() as conn:
                conn.execute(
                    text(f"""
                        INSERT INTO {SCHEMA_NAME}.trade_records 
                        (trade_date, trade_time, code, name, action, price, quantity, 
                         amount, commission, stamp_tax, pnl, pnl_pct, reason)
                        VALUES (CURRENT_DATE, NOW(), :code, :name, :action, :price, :quantity,
                                :amount, :commission, :stamp_tax, :pnl, :pnl_pct, :reason)
                    """),
                    {
                        'code': code, 'name': name, 'action': action,
                        'price': price, 'quantity': quantity,
                        'amount': amount, 'commission': commission,
                        'stamp_tax': stamp_tax, 'pnl': pnl,
                        'pnl_pct': pnl_pct, 'reason': reason
                    }
                )
                conn.commit()
        except Exception as e:
            logger.error(f"保存交易记录失败: {e}")
    
    def _get_positions_dict(self):
        """获取持仓字典"""
        positions = self._get_all_positions()
        return {p['code']: p for p in positions}
    
    def update_prices(self):
        """更新所有持仓的实时价格和盈亏"""
        positions = self._get_all_positions()
        codes = [p['code'] for p in positions]
        
        if not codes:
            return
        
        quotes = fetch_realtime_quotes(codes)
        
        for code, pos in self._get_positions_dict().items():
            quote = quotes.get(code, {})
            current_price = quote.get('price', 0)
            
            if current_price > 0:
                cost_price = pos['cost_price']
                quantity = pos['quantity']
                market_value = current_price * quantity
                total_cost = cost_price * quantity
                pnl = market_value - total_cost
                pnl_pct = (pnl / total_cost) * 100 if total_cost > 0 else 0
                
                self._update_position(
                    code, pos['name'], quantity, cost_price,
                    current_price, market_value, pnl, pnl_pct
                )
        
        # 更新账户状态 - 使用正确的总盈亏计算方式
        account = self._get_account_state()
        if account:
            positions = self._get_all_positions()
            total_market_value = sum([p['market_value'] for p in positions])
            total_cost = sum([p['cost_price'] * p['quantity'] for p in positions])
            
            # 正确的总盈亏 = 总资产 - 初始本金
            # 总资产 = 现金 + 持仓市值
            cash = account['cash']
            total_value = cash + total_market_value
            initial_capital = account['initial_capital']
            
            # 总盈亏（包含已实现盈亏）
            total_pnl = total_value - initial_capital
            total_pnl_pct = (total_pnl / initial_capital) * 100 if initial_capital > 0 else 0
            
            self._update_account_state(cash, total_value, total_pnl, total_pnl_pct)
    
    def check_stop_conditions(self):
        """检查所有持仓的止盈止损"""
        results = []
        positions = self._get_all_positions()
        
        for pos in positions:
            current_price = pos['market_price']
            cost_price = pos['cost_price']
            pnl_pct = (current_price - cost_price) / cost_price * 100 if cost_price > 0 else 0
            
            if pnl_pct <= -7.0:
                success, msg = self.sell(pos['code'], current_price, pos['quantity'], f"止损触发: {pnl_pct:.2f}%")
                results.append({'code': pos['code'], 'action': 'stop_loss', 'success': success, 'message': msg})
            elif pnl_pct >= 15.0:
                success, msg = self.sell(pos['code'], current_price, pos['quantity'], f"止盈触发: {pnl_pct:.2f}%")
                results.append({'code': pos['code'], 'action': 'take_profit', 'success': success, 'message': msg})
        
        return results
    
    def get_status(self):
        """获取账户状态"""
        account = self._get_account_state()
        positions = self._get_all_positions()
        
        if account:
            return {
                'cash': round(account['cash'], 2),
                'total_value': round(account['total_value'], 2),
                'total_pnl': round(account['total_pnl'], 2),
                'total_pnl_pct': round(account['total_pnl_pct'], 2),
                'position_count': len(positions),
                'positions': positions
            }
        return None


# 全局账户实例
_sim_account = None

def get_sim_account():
    global _sim_account
    if _sim_account is None:
        _sim_account = SimulatedAccountDB()
    return _sim_account


# ========== 自动交易 API 接口 ==========

@app.post("/api/auto-trade/execute")
async def execute_auto_trade(request: Request):
    """执行自动交易"""
    try:
        body = await request.json()
        action = body.get('action', 'all')
        code = body.get('code')
        price = body.get('price')
        quantity = body.get('quantity')
        reason = body.get('reason', '')
        
        account = get_sim_account()
        trades = []
        
        if action == 'buy':
            if not code or not price or not quantity:
                return {"code": 400, "message": "缺少必要参数"}
            name = body.get('name', code)
            success, msg = account.buy(code, name, float(price), int(quantity), reason)
            return {"code": 200 if success else 400, "message": msg}
        
        elif action == 'sell':
            if not code:
                return {"code": 400, "message": "缺少股票代码"}
            pos = account._get_position(code)
            if not pos:
                return {"code": 404, "message": "持仓不存在"}
            if not price:
                price = pos['market_price']
            if not quantity:
                quantity = pos['quantity']
            success, msg = account.sell(code, float(price), int(quantity), reason)
            return {"code": 200 if success else 400, "message": msg}
        
        elif action == 'check_stop':
            results = account.check_stop_conditions()
            account.update_prices()
            return {"code": 200, "data": results}
        
        elif action == 'refresh':
            account.update_prices()
            return {"code": 200, "message": "价格已刷新"}
        
        elif action == 'auto_buy':
            from db_manager import get_latest_selected_stocks
            df = get_latest_selected_stocks(limit=30, fallback_to_prev=True)
            
            if df.empty:
                return {"code": 200, "message": "无选股信号", "trades": [], "data_date": None, "is_fallback": False}
            
            data_date = df.iloc[0].get('date', '') if 'date' in df.columns else ''
            is_fallback = data_date != datetime.now().strftime("%Y-%m-%d")
            
            account_state = account._get_account_state()
            cash = account_state['cash'] if account_state else 100000
            
            for _, row in df.iterrows():
                code = str(row.get('code', '')).zfill(6)
                name = row.get('name', '')
                score = row.get('total_score', 0)
                price = row.get('price', 0)
                
                if code in account._get_positions_dict():
                    continue
                if score < 50:
                    continue
                if price <= 0:
                    continue
                
                shares = int(min(10000, cash * 0.2) / price / 100) * 100
                shares = max(shares, 100)
                
                if cash < price * shares:
                    continue
                
                current_price = account._get_current_price(code)
                if current_price and current_price > 0:
                    price = current_price
                
                success, msg = account.buy(code, name, price, shares, f"自动买入 评分{score:.1f}")
                trades.append({
                    "action": "buy", 
                    "code": code, 
                    "name": name,
                    "price": price,
                    "quantity": shares,
                    "success": success, 
                    "message": msg
                })
                
                if success:
                    account_state = account._get_account_state()
                    cash = account_state['cash'] if account_state else 0
            
            account.update_prices()
            
            return {
                "code": 200,
                "message": f"自动买入完成，执行 {len(trades)} 笔",
                "trades": trades,
                "data_date": str(data_date) if data_date else None,
                "is_fallback": is_fallback
            }
        
        elif action == 'all':
            # 1. 先检查止盈止损（自动执行）
            stop_results = account.check_stop_conditions()
            for r in stop_results:
                trades.append({
                    "action": r['action'],
                    "code": r['code'],
                    "success": r['success'],
                    "message": r['message']
                })
    
            
            account.update_prices()
            
            from db_manager import get_latest_selected_stocks
            df = get_latest_selected_stocks(limit=30, fallback_to_prev=True)
            
            data_date = None
            is_fallback = False
            
            if not df.empty:
                data_date = df.iloc[0].get('date', '') if 'date' in df.columns else ''
                is_fallback = data_date != datetime.now().strftime("%Y-%m-%d")
                
                account_state = account._get_account_state()
                cash = account_state['cash'] if account_state else 100000
                
                for _, row in df.iterrows():
                    code = str(row.get('code', '')).zfill(6)
                    name = row.get('name', '')
                    score = row.get('total_score', 0)
                    price = row.get('price', 0)
                    
                    if code in account._get_positions_dict():
                        continue
                    if score < 50:
                        continue
                    if price <= 0:
                        continue
                    
                    shares = int(min(10000, cash * 0.2) / price / 100) * 100
                    shares = max(shares, 100)
                    
                    if cash < price * shares:
                        continue
                    
                    current_price = account._get_current_price(code)
                    if current_price and current_price > 0:
                        price = current_price
                    
                    success, msg = account.buy(code, name, price, shares, f"自动买入 评分{score:.1f}")
                    trades.append({
                        "action": "buy", 
                        "code": code, 
                        "name": name,
                        "price": price,
                        "quantity": shares,
                        "success": success, 
                        "message": msg
                    })
                    
                    if success:
                        account_state = account._get_account_state()
                        cash = account_state['cash'] if account_state else 0
            
            account.update_prices()
            status = account.get_status()
            
            return {
                "code": 200,
                "message": f"自动交易完成，执行 {len(trades)} 笔",
                "trades": trades,
                "account": status,
                "data_date": str(data_date) if data_date else None,
                "is_fallback": is_fallback
            }
        
        else:
            return {"code": 400, "message": f"未知操作: {action}"}
            
    except Exception as e:
        logger.error(f"自动交易错误: {e}")
        import traceback
        traceback.print_exc()
        return {"code": 500, "message": str(e)}


@app.get("/api/auto-trade/status")
async def get_auto_trade_status():
    """获取自动交易状态"""
    try:
        account = get_sim_account()
        
        # 更新实时价格（保持持仓价格最新）
        account.update_prices()
        
        # 获取持仓列表（含实时价格）
        positions = account._get_all_positions()
        
        # ========== 从 account_state 表获取总盈亏数据 ==========
        account_state = account._get_account_state()
        
        if account_state:
            # 使用 account_state 中的总数据（包含已实现盈亏）
            total_pnl = account_state['total_pnl']
            total_pnl_pct = account_state['total_pnl_pct']
            total_value = account_state['total_value']
            cash = account_state['cash']
            initial_capital = account_state.get('initial_capital', 100000)
            logger.info(f"从 account_state 读取: total_pnl={total_pnl}, total_pnl_pct={total_pnl_pct}")
        else:
            # 如果没有 account_state 数据，从持仓计算
            total_market_value = sum([p['market_value'] for p in positions])
            total_cost = sum([p['cost_price'] * p['quantity'] for p in positions])
            total_pnl = sum([p['pnl'] for p in positions])
            total_value = account.cash + total_market_value
            total_pnl_pct = (total_pnl / 100000) * 100 if 100000 > 0 else 0
            cash = account.cash
            initial_capital = 100000
            logger.info("account_state 无数据，从持仓计算")
        
        today = datetime.now().strftime("%Y-%m-%d")
        
        # 查询今日交易次数
        with account.engine.connect() as conn:
            result = conn.execute(
                text(f"""
                    SELECT COUNT(*) 
                    FROM {SCHEMA_NAME}.trade_records 
                    WHERE DATE(created_at) = :today
                """),
                {'today': today}
            )
            daily_count = result.fetchone()[0]
        
        # 构建返回数据（同时包含总盈亏和浮动盈亏供前端使用）
        total_market_value = sum([p['market_value'] for p in positions])
        float_pnl = sum([p['pnl'] for p in positions])
        
        return {
            "code": 200,
            "data": {
                # 账户总览（从 account_state 读取）
                "cash": round(cash, 2),
                "total_value": round(total_value, 2),
                "total_pnl": round(total_pnl, 2),           # 总盈亏（包含已实现）
                "total_pnl_pct": round(total_pnl_pct, 2),    # 总收益率
                "initial_capital": round(initial_capital, 2),
                "snapshot_date": account_state.get('snapshot_date', today) if account_state else today,
                # 持仓信息
                "position_count": len(positions),
                "positions": positions,
                "daily_trade_count": daily_count,
                "last_trade_date": today,
                # 浮动盈亏（当前持仓浮动盈亏，供参考）
                "float_pnl": round(float_pnl, 2),
                "float_pnl_pct": round((float_pnl / 100000) * 100, 2) if 100000 > 0 else 0
            }
        }
    except Exception as e:
        logger.error(f"获取交易状态错误: {e}")
        import traceback
        traceback.print_exc()
        return {"code": 500, "message": str(e)}


@app.post("/api/auto-trade/clear")
async def clear_all_positions():
    """清空所有持仓"""
    try:
        account = get_sim_account()
        positions = account._get_all_positions()
        
        for pos in positions:
            account.sell(pos['code'], pos['market_price'], pos['quantity'], "手动清仓")
        
        account.update_prices()
        status = account.get_status()
        
        return {"code": 200, "message": f"已清仓 {len(positions)} 只股票", "account": status}
    except Exception as e:
        logger.error(f"清仓错误: {e}")
        return {"code": 500, "message": str(e)}


@app.get("/api/auto-trade/trades")
async def get_trade_records(
    limit: int = Query(50, ge=1, le=200),
    code: Optional[str] = Query(None, description="筛选股票代码"),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD")
):
    """获取交易记录"""
    try:
        engine = get_sync_engine()
        
        with engine.connect() as conn:
            sql = f"""
                SELECT id, trade_date, trade_time, code, name, action, 
                       price, quantity, amount, commission, stamp_tax, 
                       pnl, pnl_pct, reason
                FROM {SCHEMA_NAME}.trade_records
                WHERE 1=1
            """
            params = {}
            
            if code:
                sql += " AND code = :code"
                params['code'] = code
            
            if start_date:
                sql += " AND trade_date >= :start_date"
                params['start_date'] = start_date
            
            if end_date:
                sql += " AND trade_date <= :end_date"
                params['end_date'] = end_date
            
            sql += " ORDER BY trade_date DESC, id DESC LIMIT :limit"
            params['limit'] = limit
            
            result = conn.execute(text(sql), params)
            rows = result.fetchall()
            
            records = []
            for row in rows:
                records.append({
                    'id': row[0],
                    'trade_date': str(row[1]) if row[1] else '',
                    'trade_time': str(row[2]) if row[2] else '',
                    'code': row[3] or '',
                    'name': row[4] or '',
                    'action': row[5] or '',
                    'price': float(row[6]) if row[6] else 0,
                    'quantity': float(row[7]) if row[7] else 0,
                    'amount': float(row[8]) if row[8] else 0,
                    'commission': float(row[9]) if row[9] else 0,
                    'stamp_tax': float(row[10]) if row[10] else 0,
                    'pnl': float(row[11]) if row[11] else 0,
                    'pnl_pct': float(row[12]) if row[12] else 0,
                    'reason': row[13] or ''
                })
            
            return {"code": 200, "data": records, "count": len(records)}
            
    except Exception as e:
        logger.error(f"获取交易记录错误: {e}")
        return {"code": 500, "message": str(e), "data": []}


@app.get("/api/auto-trade/trades/stats")
async def get_trade_stats():
    """获取交易统计"""
    try:
        engine = get_sync_engine()
        
        with engine.connect() as conn:
            # 总统计
            result = conn.execute(
                text(f"""
                    SELECT 
                        COUNT(*) as total_trades,
                        SUM(CASE WHEN action LIKE '%买入%' OR action = '买入' THEN 1 ELSE 0 END) as buy_count,
                        SUM(CASE WHEN action LIKE '%卖出%' OR action = '卖出' OR action LIKE '%清仓%' THEN 1 ELSE 0 END) as sell_count,
                        SUM(amount) as total_amount,
                        SUM(commission) as total_commission,
                        SUM(stamp_tax) as total_stamp_tax,
                        SUM(pnl) as total_pnl,
                        COALESCE(AVG(pnl_pct), 0) as avg_pnl_pct
                    FROM {SCHEMA_NAME}.trade_records
                """)
            )
            row = result.fetchone()
            
            # 今日统计
            today = datetime.now().strftime("%Y-%m-%d")
            result_today = conn.execute(
                text(f"""
                    SELECT 
                        COUNT(*) as today_trades,
                        SUM(CASE WHEN action LIKE '%买入%' OR action = '买入' THEN 1 ELSE 0 END) as today_buy,
                        SUM(CASE WHEN action LIKE '%卖出%' OR action = '卖出' OR action LIKE '%清仓%' THEN 1 ELSE 0 END) as today_sell,
                        COALESCE(SUM(pnl), 0) as today_pnl
                    FROM {SCHEMA_NAME}.trade_records
                    WHERE trade_date = :today
                """),
                {'today': today}
            )
            row_today = result_today.fetchone()
            
            # 按股票统计
            result_stocks = conn.execute(
                text(f"""
                    SELECT 
                        code, name,
                        COUNT(*) as trade_count,
                        SUM(CASE WHEN action LIKE '%买入%' OR action = '买入' THEN quantity ELSE 0 END) as buy_quantity,
                        SUM(CASE WHEN action LIKE '%卖出%' OR action = '卖出' OR action LIKE '%清仓%' THEN quantity ELSE 0 END) as sell_quantity,
                        COALESCE(SUM(pnl), 0) as total_pnl
                    FROM {SCHEMA_NAME}.trade_records
                    GROUP BY code, name
                    ORDER BY total_pnl DESC
                    LIMIT 20
                """)
            )
            stocks = []
            for r in result_stocks.fetchall():
                stocks.append({
                    'code': r[0] or '',
                    'name': r[1] or '',
                    'trade_count': r[2] or 0,
                    'buy_quantity': float(r[3]) if r[3] else 0,
                    'sell_quantity': float(r[4]) if r[4] else 0,
                    'total_pnl': float(r[5]) if r[5] else 0
                })
            
            return {
                "code": 200,
                "data": {
                    "total": {
                        "trades": row[0] or 0,
                        "buy_count": row[1] or 0,
                        "sell_count": row[2] or 0,
                        "total_amount": float(row[3]) if row[3] else 0,
                        "total_commission": float(row[4]) if row[4] else 0,
                        "total_stamp_tax": float(row[5]) if row[5] else 0,
                        "total_pnl": float(row[6]) if row[6] else 0,
                        "avg_pnl_pct": float(row[7]) if row[7] else 0  # 确保返回 0 而不是 null
                    },
                    "today": {
                        "trades": row_today[0] or 0,
                        "buy_count": row_today[1] or 0,
                        "sell_count": row_today[2] or 0,
                        "today_pnl": float(row_today[3]) if row_today[3] else 0
                    },
                    "top_stocks": stocks
                }
            }
            
    except Exception as e:
        logger.error(f"获取交易统计错误: {e}")
        return {"code": 500, "message": str(e)}



# ========== 止盈止损配置接口 ==========

@app.get("/api/auto-trade/config")
async def get_config():
    """获取止盈止损配置"""
    try:
        from db_manager import get_stop_loss_config
        config = get_stop_loss_config()
        if config:
            return {"code": 200, "data": config}
        else:
            return {"code": 404, "message": "配置不存在"}
    except Exception as e:
        logger.error(f"获取配置错误: {e}")
        return {"code": 500, "message": str(e)}


@app.post("/api/auto-trade/config")
async def save_config(request: Request):
    """保存止盈止损配置"""
    try:
        body = await request.json()
        from db_manager import save_stop_loss_config
        
        config = {
            'stop_loss_pct': float(body.get('stop_loss_pct', -7.0)),
            'take_profit_pct': float(body.get('take_profit_pct', 15.0)),
            'max_daily_trades': int(body.get('max_daily_trades', 20)),
            'single_buy_amount': float(body.get('single_buy_amount', 10000)),
            'min_buy_score': float(body.get('min_buy_score', 50))
        }
        
        if save_stop_loss_config(config):
            return {"code": 200, "message": "配置保存成功", "data": config}
        else:
            return {"code": 500, "message": "配置保存失败"}
    except Exception as e:
        logger.error(f"保存配置错误: {e}")
        return {"code": 500, "message": str(e)}


@app.get("/api/auto-trade/config/default")
async def get_default_config():
    """获取默认配置（用于重置）"""
    return {
        "code": 200,
        "data": {
            "stop_loss_pct": -7.0,
            "take_profit_pct": 15.0,
            "max_daily_trades": 20,
            "single_buy_amount": 10000.00,
            "min_buy_score": 50.00
        }
    }




# ========== 加仓/减仓接口 ==========

@app.post("/api/auto-trade/add-position")
async def add_position(request: Request):
    """加仓"""
    try:
        body = await request.json()
        code = body.get('code')
        quantity = body.get('quantity')
        price = body.get('price')
        reason = body.get('reason', '手动加仓')
        
        if not code or not quantity or not price:
            return {"code": 400, "message": "缺少必要参数"}
        
        account = get_sim_account()
        
        # 获取持仓信息
        pos = account._get_position(code)
        if not pos:
            return {"code": 404, "message": "持仓不存在"}
        
        success, msg = account.buy(code, pos['name'], float(price), int(quantity), reason)
        
        if success:
            # 更新价格
            account.update_prices()
            status = account.get_status()
            return {"code": 200, "message": msg, "account": status}
        else:
            return {"code": 400, "message": msg}
            
    except Exception as e:
        logger.error(f"加仓错误: {e}")
        return {"code": 500, "message": str(e)}


@app.post("/api/auto-trade/reduce-position")
async def reduce_position(request: Request):
    """减仓"""
    try:
        body = await request.json()
        code = body.get('code')
        quantity = body.get('quantity')
        price = body.get('price')
        reason = body.get('reason', '手动减仓')
        
        if not code or not quantity or not price:
            return {"code": 400, "message": "缺少必要参数"}
        
        account = get_sim_account()
        
        # 获取持仓信息
        pos = account._get_position(code)
        if not pos:
            return {"code": 404, "message": "持仓不存在"}
        
        if int(quantity) >= pos['quantity']:
            return {"code": 400, "message": f"减仓数量不能大于或等于持仓数量（当前持仓 {pos['quantity']} 股）"}
        
        success, msg = account.sell(code, float(price), int(quantity), reason)
        
        if success:
            account.update_prices()
            status = account.get_status()
            return {"code": 200, "message": msg, "account": status}
        else:
            return {"code": 400, "message": msg}
            
    except Exception as e:
        logger.error(f"减仓错误: {e}")
        return {"code": 500, "message": str(e)}


@app.post("/api/auto-trade/clear-position")
async def clear_position(request: Request):
    """清仓（卖出全部持仓）"""
    try:
        body = await request.json()
        code = body.get('code')
        price = body.get('price')
        reason = body.get('reason', '手动清仓')
        
        if not code:
            return {"code": 400, "message": "缺少股票代码"}
        
        account = get_sim_account()
        
        # 获取持仓信息
        pos = account._get_position(code)
        if not pos:
            return {"code": 404, "message": "持仓不存在"}
        
        if not price:
            price = pos['market_price']
        
        success, msg = account.sell(code, float(price), pos['quantity'], reason)
        
        if success:
            account.update_prices()
            status = account.get_status()
            return {"code": 200, "message": msg, "account": status}
        else:
            return {"code": 400, "message": msg}
            
    except Exception as e:
        logger.error(f"清仓错误: {e}")
        return {"code": 500, "message": str(e)}



# ========== 企业微信推送 ==========
def push_to_wechat(message: str) -> None:
    """推送消息到企业微信"""
    webhook_url = os.environ.get("WECHAT_WEBHOOK_URL", "")
    if not webhook_url:
        logger.warning("企业微信 Webhook URL 未配置")
        return
    
    try:
        # 如果消息是列表，拼接成字符串
        if isinstance(message, list):
            message = "\n".join(message)
        
        # 限制消息长度（企业微信限制）
        if len(message) > 2000:
            message = message[:1997] + "..."
        
        headers = {'Content-Type': 'application/json'}
        data = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"## 📊 自动止盈止损通知\n\n{message}"
            }
        }
        resp = requests.post(webhook_url, json=data, headers=headers, timeout=5)
        if resp.status_code == 200:
            result = resp.json()
            if result.get('errcode') == 0:
                logger.info("企业微信推送成功")
            else:
                logger.warning(f"企业微信推送失败: {result}")
        else:
            logger.warning(f"企业微信推送失败: HTTP {resp.status_code}")
    except Exception as e:
        logger.error(f"企业微信推送异常: {e}")

# ========== 自动止盈止损定时任务 ==========

# 全局任务状态
_auto_stop_task_running = False
_auto_stop_last_run = None

def auto_check_stop_loss():
    """定时检查止盈止损（每5分钟执行一次）"""
    global _auto_stop_task_running, _auto_stop_last_run
    
    # 防止重复执行
    if _auto_stop_task_running:
        logger.info("上一次止盈止损检查还在运行，跳过本次")
        return
    
    try:
        _auto_stop_task_running = True
        logger.info("开始执行定时止盈止损检查...")
        
        account = get_sim_account()
        
        # 检查止盈止损
        results = account.check_stop_conditions()
        
        if results:
            # 有交易发生，推送通知
            success_results = [r for r in results if r.get('success')]
            if success_results:
                # 构建推送消息
                messages = ["**止盈止损执行结果：**", ""]
                for r in success_results:
                    emoji = "🔴" if "止损" in r['message'] else "🟢"
                    messages.append(f"{emoji} {r['message']}")
                
                # 更新账户信息
                status = account.get_status()
                if status:
                    messages.append("")
                    messages.append(f"**当前账户：**")
                    messages.append(f"总资产：{status.get('total_value', 0):.2f}")
                    messages.append(f"总盈亏：{status.get('total_pnl', 0):+.2f}")
                    messages.append(f"收益率：{status.get('total_pnl_pct', 0):+.2f}%")
                    messages.append(f"持仓数量：{status.get('position_count', 0)} 只")
                
                # 推送
                push_to_wechat(messages)
            else:
                logger.info("止盈止损检查完成，无成功交易")
        else:
            logger.info("止盈止损检查完成，无触发条件")
        
        _auto_stop_last_run = datetime.now()
        
    except Exception as e:
        logger.error(f"定时止盈止损检查失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        _auto_stop_task_running = False


def start_stop_loss_scheduler():
    """启动止盈止损定时任务"""
    scheduler = BackgroundScheduler()
    
    # 每5分钟执行一次
    scheduler.add_job(
        func=auto_check_stop_loss,
        trigger=IntervalTrigger(minutes=5),
        id='auto_stop_loss_check',
        replace_existing=True,
        max_instances=1
    )
    
    scheduler.start()
    logger.info("止盈止损定时任务已启动（每5分钟检查一次）")
    
    # 注册关闭钩子
    atexit.register(lambda: scheduler.shutdown())
    
    return scheduler




# ========== 启动事件 ==========
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Stock Watch System...")
    await init_database()
    
    # 测试 Redis 连接
    try:
        redis_client.ping()
        logger.info("Redis 连接成功")
    except Exception as e:
        logger.error(f"Redis 连接失败: {e}")
    
    # 启动止盈止损定时任务
    scheduler = start_stop_loss_scheduler()
    
    logger.info("Stock Watch System ready!")
    yield
    
    # 关闭时清理
    if scheduler:
        scheduler.shutdown()
    if db_pool:
        await db_pool.close()


@app.post("/api/auto-trade/stop-loss/trigger")
async def trigger_stop_loss_check():
    """手动触发止盈止损检查"""
    try:
        account = get_sim_account()
        results = account.check_stop_conditions()
        
        if results:
            success_results = [r for r in results if r.get('success')]
            return {
                "code": 200,
                "message": f"止盈止损检查完成，成功 {len(success_results)} 笔",
                "data": results
            }
        else:
            return {"code": 200, "message": "无止盈止损触发条件", "data": []}
    except Exception as e:
        logger.error(f"手动触发止盈止损失败: {e}")
        return {"code": 500, "message": str(e)}


@app.get("/api/auto-trade/stop-loss/status")
async def get_stop_loss_status():
    """获取止盈止损定时任务状态"""
    return {
        "code": 200,
        "data": {
            "is_running": _auto_stop_task_running,
            "last_run": str(_auto_stop_last_run) if _auto_stop_last_run else None,
            "interval": "5分钟"
        }
    }


# ========== 启动事件 ==========
# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     logger.info("Starting Stock Watch System...")
#     await init_database()
#     # 测试 Redis 连接
#     try:
#         redis_client.ping()
#         logger.info("Redis 连接成功")
#     except Exception as e:
#         logger.error(f"Redis 连接失败: {e}")
#     logger.info("Stock Watch System ready!")
#     yield
#     if db_pool:
#         await db_pool.close()

# app.router.lifespan_context = lifespan

# ========== 主入口 ==========
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8081))
    uvicorn.run(app, host="0.0.0.0", port=port)
