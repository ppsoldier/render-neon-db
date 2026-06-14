import os
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
# 使用环境变量，支持同步和异步连接
DB_HOST = os.environ.get("DB_HOST", "ep-rapid-frog-ani7chkm.c-6.us-east-1.aws.neon.tech")
DB_USER = os.environ.get("DB_USER", "neondb_owner")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "npg_b1QR9lMdusev")
DB_NAME = os.environ.get("DB_NAME", "neondb")
DB_PORT = int(os.environ.get("DB_PORT", 5432))
# SCHEMA_NAME = os.environ.get("DB_SCHEMA", "stock_watch")
SCHEMA_NAME = os.environ.get("DB_SCHEMA", "stock_data")

# 异步数据库URL（用于asyncpg）
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?sslmode=require"

# 同步数据库URL（用于pandas/SQLAlchemy）
SYNC_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# 表名常量
TABLE_MARKET = f"{SCHEMA_NAME}.market_data"
TABLE_STOCKS = f"{SCHEMA_NAME}.stocks_data"
TABLE_CONCEPTS = f"{SCHEMA_NAME}.concepts_data"
TABLE_INDUSTRIES = f"{SCHEMA_NAME}.industries_data"
TABLE_SELECTED = f"{SCHEMA_NAME}.selected_stocks"
TABLE_WATCHLIST = f"{SCHEMA_NAME}.watchlist"
TABLE_CURRENT_POSITIONS = f"{SCHEMA_NAME}.current_positions"


# ========== 同步数据库引擎（用于pandas）==========
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

# ========== 数据库操作函数 ==========
async def init_database():
    """初始化数据库：创建 schema 和所有表"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA_NAME}")
        await conn.execute(f"SET search_path TO {SCHEMA_NAME}")
        #数据库表已经创建
        logger.info(f"数据库初始化完成 (Schema: {SCHEMA_NAME})")

# ========== 数据保存函数 ==========
async def save_market_data(market_result: dict, data_date: str = None):
    """保存市场数据"""
    if data_date is None:
        data_date = datetime.now().strftime("%Y-%m-%d")
    
    pool = await get_db()
    async with pool.acquire() as conn:
        # 删除当天旧数据
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
    """保存选股结果"""
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

async def save_operation_log(content: str, log_level: str = "INFO", module: str = "stock_watch"):
    """保存操作日志"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute(f"""
            INSERT INTO {TABLE_OPERATION_LOG} (log_date, log_level, module, content)
            VALUES ($1, $2, $3, $4)
        """, datetime.now().strftime("%Y-%m-%d"), log_level, module, content[:2000])

# ========== 九方智投签名算法 ==========
# ... (保持原有的签名函数不变) ...

def generate_signature(listed_sector, sort_field, sort_type, timestamp, page):
    base_string = f"sjdxfnqogbzoun13d971ckh8p{listed_sector}{page}20{sort_field}{sort_type}{timestamp}"
    return hashlib.md5(base_string.encode()).hexdigest()

def get_sector_signature(time_stamp):
    base_string = f"sjdxfnqogbzoun13d971ckh8p{time_stamp}"
    return hashlib.md5(base_string.encode()).hexdigest()

def get_research_signature(params):
    secret = "sjdxfnqogbzoun13d971ckh8p"
    timestamp = str(int(time.time() * 1000))
    sorted_keys = sorted(params.keys())
    values_str = "".join([params[k] for k in sorted_keys])
    sign_str = secret + values_str + timestamp
    sign = hashlib.md5(sign_str.encode()).hexdigest()
    return sign, timestamp

# ========== 实时数据采集函数（保持原有）==========
async def fetch_stock_rank(sort_type: str):
    """获取股票涨跌幅排行"""
    # ... 保持原有实现 ...
    stock_rank = []
    for page in range(1, 9):
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
            data = response.json()
            
            if not data or 'data' not in data or 'infos' not in data['data']:
                break
            
            infos = data['data']['infos']
            for item in infos:
                price = item.get('closePx')
                if price is None:
                    price = item.get('lastPx', 0)
                if price is None:
                    price = 0
                
                stock_rank.append({
                    "stock_code": item.get('symbol', ''),
                    "stock_name": item.get('prodName', ''),
                    "price": float(price) if price else 0,
                    "change_percent": round(float(item.get('pxChangeRate', 0)) * 100, 2) if item.get('pxChangeRate') else 0,
                    "volume": item.get('businessAmount', 0),
                    "amount": item.get('businessBalance', 0)
                })
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"获取股票排行错误: {e}")
            continue
    
    return stock_rank

# ========== 九方智投数据采集函数 ==========

async def fetch_sector_rank(hq_type_code: str):
    """获取行业/概念板块排行
    hq_type_code: 'HY' 行业, 'GN' 概念
    """
    sector_rank = []
    for page in range(1, 4):
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
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }
        
        url = 'https://hq.chongnengjihua.com/rjhy-quote-sector/api/1/pc/plate/block/quote/list'
        
        try:
            response = requests.get(url=url, headers=headers, params=params, timeout=10)
            data = response.json()
            
            if not data or 'data' not in data or 'plate' not in data['data']:
                break
            
            plates = data['data']['plate']
            for item in plates:
                # 获取价格和涨跌幅
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
            continue
    
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

# ========== 实时行情接口 ==========
@app.get("/api/realtime/ranks")
async def get_realtime_ranks(rank_type: str = Query("up", description="up/down")):
    """获取实时涨跌幅榜单"""
    sort_type = '0' if rank_type == 'up' else '1'
    try:
        ranks = await fetch_stock_rank(sort_type)
        return {
            "code": 200,
            "message": "success",
            "data": ranks,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        logger.error(f"获取实时榜单错误: {e}")
        return {"code": 500, "message": str(e), "data": []}

@app.get("/api/realtime/industry")
async def get_realtime_industry():
    """获取实时行业板块排行"""
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
    """获取实时概念板块排行"""
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
    """获取涨跌停统计"""
    try:
        # 从涨幅榜和跌幅榜中统计涨停/跌停数量
        up_ranks = await fetch_stock_rank('0')
        down_ranks = await fetch_stock_rank('1')
        
        # 涨停判断：涨幅 >= 9.5%（主板）或 >= 19.5%（创业板/科创板）
        limit_up = 0
        limit_down = 0
        
        for stock in up_ranks:
            change = stock.get('change_percent', 0)
            code = stock.get('stock_code', '')
            if code.startswith(('30', '68')):
                if change >= 19.6:
                    limit_up += 1
            elif code.startswith('8'):
                if change >= 29.6:
                    limit_up += 1
            else:
                if change >= 9.6:
                    limit_up += 1
        
        for stock in down_ranks:
            change = stock.get('change_percent', 0)
            code = stock.get('stock_code', '')
            if code.startswith(('30', '68')):
                if change <= -19.6:
                    limit_down += 1
            elif code.startswith('8'):
                if change <= -29.6:
                    limit_down += 1
            else:
                if change <= -9.6:
                    limit_down += 1
        
        # 市场情绪评分（0-100）
        sentiment = 50 + (limit_up - limit_down) * 2
        sentiment = max(0, min(100, sentiment))
        
        return {
            "limit_up_count": limit_up,
            "limit_down_count": limit_down,
            "sentiment": sentiment
        }
    except Exception as e:
        logger.error(f"获取涨跌停统计错误: {e}")
        # 返回默认值
        return {"limit_up_count": 0, "limit_down_count": 0, "sentiment": 50}

def load_stock_mapping():
    """加载股票名称映射表（支持拼音首字母）"""
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
                
                # 格式: py_code -> code,name,price
                parts = line.split('->')
                if len(parts) != 2:
                    continue
                
                py_code = parts[0].strip().upper()  # 拼音首字母，转大写
                right_part = parts[1].strip()
                right_parts = right_part.split(',')
                
                if len(right_parts) >= 2:
                    code = right_parts[0].strip()
                    name = right_parts[1].strip()
                    price = right_parts[2].strip() if len(right_parts) > 2 else ''
                    
                    # 清理名称中的特殊字符
                    name = name.replace('*', '').replace('ST', '').strip()
                    
                    STOCK_MAPPING[code] = name
                    STOCK_MAPPING_BY_CODE[code] = name
                    STOCK_MAPPING_BY_NAME[name] = code
                    
                    # 拼音首字母映射（支持多个股票对应同一个拼音）
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
    """根据关键词搜索股票（支持代码、名称、拼音首字母）"""
    results = []
    keyword_upper = keyword.strip().upper()
    keyword_lower = keyword.strip().lower()
    
    if not keyword_upper:
        return results
    
    # 1. 拼音首字母精确匹配（支持多个结果）
    if keyword_upper in STOCK_MAPPING_BY_PY:
        for stock in STOCK_MAPPING_BY_PY[keyword_upper]:
            results.append({
                "stock_code": stock["code"],
                "stock_name": stock["name"],
                "price": stock.get("price", "")
            })
        if len(results) >= limit:
            return results[:limit]
    
    # 2. 股票代码精确匹配
    if keyword_lower in STOCK_MAPPING_BY_CODE:
        results.append({
            "stock_code": keyword_lower,
            "stock_name": STOCK_MAPPING_BY_CODE[keyword_lower],
            "price": ""
        })
        if len(results) >= limit:
            return results[:limit]
    
    # 3. 股票代码前缀匹配
    for code, name in STOCK_MAPPING_BY_CODE.items():
        if code.startswith(keyword_lower):
            results.append({"stock_code": code, "stock_name": name, "price": ""})
            if len(results) >= limit:
                break
    
    if len(results) >= limit:
        return results[:limit]
    
    # 4. 股票名称模糊匹配
    for code, name in STOCK_MAPPING_BY_CODE.items():
        if keyword_lower in name.lower():
            results.append({"stock_code": code, "stock_name": name, "price": ""})
            if len(results) >= limit:
                break
    
    # 5. 去重（按股票代码）
    seen = set()
    unique_results = []
    for r in results:
        if r["stock_code"] not in seen:
            seen.add(r["stock_code"])
            unique_results.append(r)
    
    return unique_results[:limit]


# 应用启动时加载映射表
load_stock_mapping()


@app.get("/api/stock/search")
async def search_stock(keyword: str = Query(..., description="搜索关键词")):
    """搜索股票（支持代码、名称、拼音首字母）"""
    try:
        # 优先使用映射表搜索
        results = search_stock_by_keyword(keyword)
        
        if results:
            return {"code": 200, "data": results, "message": "success", "source": "mapping"}
        
        # 如果映射表没有结果，从数据库查询
        engine = get_sync_engine()
        with engine.connect() as conn:
            latest = conn.execute(
                text(f"SELECT MAX(date) FROM {TABLE_STOCKS}")
            ).fetchone()[0]
            
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



# ========== 选股结果接口（从数据库读取）==========
@app.get("/api/stock/picks")
async def get_stock_picks():
    """获取今日选股结果"""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        engine = get_sync_engine()
        
        # 使用原生 SQL 查询，避免 pandas 的类型问题
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
    """获取市场分析数据"""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        engine = get_sync_engine()
        
        with engine.connect() as conn:
            result = conn.execute(
                text(f"""
                    SELECT market_state, market_score, position_ratio, advice, 
                           trend_strength, volatility, ma_arrangement
                    FROM {TABLE_MARKET}
                    WHERE date = :date
                """),
                {"date": today}
            )
            row = result.fetchone()
        
        if not row:
            return {"code": 200, "data": None, "msg": "暂无市场数据"}
        
        market_data = {
            "market_state": row[0],
            "market_score": row[1],
            "position_ratio": float(row[2]) if row[2] else 0.5,
            "advice": row[3] or '',
            "trend_strength": float(row[4]) if row[4] else 0,
            "volatility": float(row[5]) if row[5] else 0,
            "ma_arrangement": row[6] or ''
        }
        
        return {"code": 200, "data": market_data}
        
    except Exception as e:
        logger.error(f"获取市场数据错误: {e}")
        return {"code": 500, "message": str(e), "data": None}

@app.get("/api/stock/sentiment")
async def get_sentiment_data():
    """获取市场情绪数据（热点概念/行业）"""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        engine = get_sync_engine()
        
        # 获取热点概念
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
        
        # 获取热点行业
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

# ========== 自选股管理接口（使用 asyncpg，最简版本）==========

@app.get("/api/watchlist")
async def get_watchlist():
    """获取自选股列表（按数据库原始顺序）"""
    try:
        pool = await get_db()
        
        async with pool.acquire() as conn:
            # 查询自选股列表，按 id 升序（原始插入顺序）
            watchlist = await conn.fetch("""
                SELECT code, name, added_date
                FROM stock_data.watchlist
                ORDER BY id ASC
            """)
            
            if not watchlist:
                return {"code": 200, "data": [], "message": "暂无自选股"}
            
            result = []
            for row in watchlist:
                code = row["code"]
                name = row["name"] if row["name"] else code
                
                # 查询最新行情
                quote = await conn.fetchrow("""
                    SELECT price, change_pct
                    FROM stock_data.stocks_data
                    WHERE code = $1
                    ORDER BY date DESC
                    LIMIT 1
                """, code)
                
                if quote:
                    price = float(quote["price"]) if quote["price"] else 0
                    change_pct = float(quote["change_pct"]) if quote["change_pct"] else 0
                else:
                    price = 0
                    change_pct = 0
                
                result.append({
                    "stock_code": code,
                    "stock_name": name,
                    "price": price,
                    "change_pct": change_pct
                })
            
            return {"code": 200, "data": result, "message": "success"}
        
    except Exception as e:
        logger.error(f"获取自选股列表错误: {e}")
        return {"code": 500, "message": str(e), "data": []}


@app.post("/api/watchlist")
async def add_watchlist(request: Request):
    """添加自选股"""
    try:
        body = await request.json()
        stock_code = body.get('stock_code')
        stock_name = body.get('stock_name')
        
        if not stock_code or not stock_name:
            return {"code": 400, "message": "股票代码和名称不能为空"}
        
        pool = await get_db()
        
        async with pool.acquire() as conn:
            # 检查是否已存在
            existing = await conn.fetchrow(
                "SELECT code FROM stock_data.watchlist WHERE code = $1",
                stock_code
            )
            if existing:
                return {"code": 400, "message": "该股票已在自选股中"}
            
            # 插入新记录
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
    """删除自选股"""
    try:
        pool = await get_db()
        
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM stock_data.watchlist WHERE code = $1",
                stock_code
            )
        
        # 检查是否删除了记录
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
    """获取持仓列表（全局共享）"""
    try:
        engine = get_sync_engine()
        
        with engine.connect() as conn:
            result = conn.execute(
                text(f"""
                    SELECT code, name, quantity, cost_price, market_price, 
                           market_value, pnl, pnl_pct, updated_at
                    FROM {TABLE_CURRENT_POSITIONS}
                    ORDER BY market_value DESC
                """)
            )
            rows = result.fetchall()
        
        holdings = []
        total_value = 0
        total_cost = 0
        
        for row in rows:
            quantity = float(row[2]) if row[2] else 0
            cost_price = float(row[3]) if row[3] else 0
            current_price = float(row[4]) if row[4] else cost_price
            market_value = float(row[5]) if row[5] else current_price * quantity
            pnl = float(row[6]) if row[6] else 0
            pnl_pct = float(row[7]) if row[7] else 0
            
            holdings.append({
                "code": row[0],
                "name": row[1],
                "quantity": quantity,
                "cost_price": cost_price,
                "current_price": current_price,
                "market_value": market_value,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "updated_at": str(row[8]) if row[8] else None
            })
            
            total_value += market_value
            total_cost += cost_price * quantity
        
        total_pnl = total_value - total_cost
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
        
        return {
            "code": 200,
            "data": holdings,
            "stats": {
                "total_value": round(total_value, 2),
                "total_cost": round(total_cost, 2),
                "total_pnl": round(total_pnl, 2),
                "total_pnl_pct": round(total_pnl_pct, 2)
            }
        }
        
    except Exception as e:
        logger.error(f"获取持仓错误: {e}")
        return {"code": 500, "message": str(e), "data": [], "stats": {}}


@app.post("/api/holdings")
async def add_holding(request: Request):
    """添加持仓（全局共享）"""
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
        except:
            pass
        
        market_value = current_price * quantity
        pnl = market_value - (cost_price * quantity)
        pnl_pct = round((pnl / (cost_price * quantity)) * 100, 2) if cost_price * quantity > 0 else 0
        
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


@app.delete("/api/holdings")
async def delete_holding(stock_code: str):
    """删除持仓"""
    try:
        engine = get_sync_engine()
        
        with engine.connect() as conn:
            conn.execute(
                text(f"DELETE FROM {TABLE_CURRENT_POSITIONS} WHERE code = :code"),
                {"code": stock_code}
            )
            conn.commit()
        
        return {"code": 200, "message": "删除成功"}
        
    except Exception as e:
        logger.error(f"删除持仓错误: {e}")
        return {"code": 500, "message": str(e)}


@app.put("/api/holdings")
async def update_holding(request: Request):
    """更新持仓（加仓/减仓）"""
    try:
        body = await request.json()
        code = body.get('code')
        action = body.get('action')  # 'add' 或 'reduce'
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


@app.delete("/api/holdings")
async def delete_holding(user_id: str, stock_code: str):
    """删除持仓"""
    try:
        engine = get_sync_engine()
        
        with engine.connect() as conn:
            conn.execute(
                text(f"DELETE FROM {TABLE_CURRENT_POSITIONS} WHERE user_id = :user_id AND code = :code"),
                {"user_id": user_id, "code": stock_code}
            )
            conn.commit()
        
        return {"code": 200, "message": "删除成功"}
        
    except Exception as e:
        logger.error(f"删除持仓错误: {e}")
        return {"code": 500, "message": str(e)}


@app.post("/api/holdings/refresh")
async def refresh_holdings_prices(user_id: str):
    """刷新所有持仓的实时价格"""
    try:
        engine = get_sync_engine()
        
        # 获取所有持仓
        with engine.connect() as conn:
            holdings = conn.execute(
                text(f"SELECT code, quantity, cost_price FROM {TABLE_CURRENT_POSITIONS} WHERE user_id = :user_id"),
                {"user_id": user_id}
            ).fetchall()
        
        if not holdings:
            return {"code": 200, "message": "无持仓数据"}
        
        # 获取实时行情
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
                        WHERE user_id = :user_id AND code = :code
                    """),
                    {
                        "current_price": round(current_price, 2),
                        "market_value": round(market_value, 2),
                        "pnl": round(pnl, 2),
                        "pnl_pct": pnl_pct,
                        "user_id": user_id,
                        "code": code
                    }
                )
            conn.commit()
        
        return {"code": 200, "message": "价格刷新成功"}
        
    except Exception as e:
        logger.error(f"刷新持仓价格错误: {e}")
        return {"code": 500, "message": str(e)}
        


# # ========== 个股详情接口 ==========
# @app.get("/api/stock/detail")
# async def get_stock_detail(stock_code: str):
#     """获取个股详情"""
#     try:
#         up_ranks = await fetch_stock_rank('0')
#         down_ranks = await fetch_stock_rank('1')
#         all_stocks = {s['stock_code']: s for s in up_ranks + down_ranks}
#         stock_info = all_stocks.get(stock_code, {})
        
#         return {
#             "stock_code": stock_code,
#             "stock_name": stock_info.get('stock_name', stock_code),
#             "quote": {
#                 "last_price": stock_info.get('price', 0),
#                 "change_percent": stock_info.get('change_percent', 0),
#                 "volume": stock_info.get('volume', 0),
#                 "amount": stock_info.get('amount', 0)
#             }
#         }
#     except Exception as e:
#         logger.error(f"获取个股详情错误: {e}")
#         return {"stock_code": stock_code, "stock_name": stock_code, "quote": {}}


@app.get("/api/stock/picks/history")
async def get_stock_picks_history(date: str = Query(..., description="日期 YYYY-MM-DD")):
    """获取指定日期的历史选股结果"""
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
    """从东方财富获取研报数据"""
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
async def get_jiufang_research(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量")
):
    """获取研报列表（从东方财富）"""
    try:
        reports = get_stock_report(page, page_size)
        
        if reports:
            return {
                "code": 200,
                "data": reports,
                "total": len(reports),
                "page": page,
                "page_size": page_size,
                "message": "success"
            }
        else:
            return {
                "code": 200,
                "data": [],
                "total": 0,
                "message": "暂无研报数据"
            }
    except Exception as e:
        logger.error(f"获取研报异常: {e}")
        return {"code": 500, "message": str(e), "data": []}


@app.get("/api/research/stock")
async def get_stock_research(
    stock_code: str = Query(..., description="股票代码"),
    limit: int = Query(20, ge=1, le=50, description="返回数量")
):
    """获取个股研报"""
    try:
        # 多抓几页筛选个股研报
        all_reports = []
        for page in range(1, 4):
            reports = get_stock_report(page, 30)
            if not reports:
                break
            all_reports.extend(reports)
            time.sleep(0.3)
        
        filtered = [r for r in all_reports if r['stock_code'] == stock_code][:limit]
        return {
            "code": 200,
            "data": filtered,
            "message": "success"
        }
    except Exception as e:
        logger.error(f"获取个股研报错误: {e}")
        return {"code": 500, "message": str(e), "data": []}


@app.get("/api/research/latest")
async def get_latest_research(limit: int = Query(20, ge=1, le=50)):
    """获取最新研报"""
    try:
        reports = get_stock_report(1, limit)
        return {
            "code": 200,
            "data": reports,
            "message": "success"
        }
    except Exception as e:
        logger.error(f"获取最新研报错误: {e}")
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
