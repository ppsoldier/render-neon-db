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

# ========== 数据库配置 ==========
# 使用环境变量，支持同步和异步连接
DB_HOST = os.environ.get("DB_HOST", "ep-rapid-frog-ani7chkm.c-6.us-east-1.aws.neon.tech")
DB_USER = os.environ.get("DB_USER", "neondb_owner")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "npg_b1QR9lMdusev")
DB_NAME = os.environ.get("DB_NAME", "neondb")
DB_PORT = int(os.environ.get("DB_PORT", 5432))
SCHEMA_NAME = os.environ.get("DB_SCHEMA", "stock_watch")

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
        
        # 市场数据表
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS market_data (
                id SERIAL PRIMARY KEY,
                date DATE NOT NULL,
                market_state VARCHAR(50),
                market_score INTEGER,
                position_ratio DECIMAL(5,2),
                advice TEXT,
                trend_strength DECIMAL(10,4),
                volatility DECIMAL(10,2),
                ma_deviation DECIMAL(10,2),
                ma_arrangement VARCHAR(50),
                index_position DECIMAL(10,2),
                recent_return DECIMAL(10,2),
                vol_ratio DECIMAL(10,2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 个股数据表
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS stocks_data (
                id SERIAL PRIMARY KEY,
                date DATE NOT NULL,
                code VARCHAR(10) NOT NULL,
                name VARCHAR(50),
                price DECIMAL(10,2),
                change_pct DECIMAL(10,2),
                market_cap DECIMAL(20,2),
                pe_ratio DECIMAL(10,2),
                turnover_rate DECIMAL(10,2),
                volume_ratio DECIMAL(10,2),
                main_inflow DECIMAL(20,2),
                amplitude DECIMAL(10,2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 概念板块数据表
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS concepts_data (
                id SERIAL PRIMARY KEY,
                date DATE NOT NULL,
                concept_name VARCHAR(100),
                concept_code VARCHAR(20),
                change_pct DECIMAL(10,2),
                leading_stock VARCHAR(50),
                stock_count INTEGER,
                up_count INTEGER,
                fundflow DECIMAL(20,2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 行业板块数据表
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS industries_data (
                id SERIAL PRIMARY KEY,
                date DATE NOT NULL,
                industry_name VARCHAR(100),
                change_pct DECIMAL(10,2),
                leading_stock VARCHAR(50),
                stock_count INTEGER,
                up_count INTEGER,
                fundflow DECIMAL(20,2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 选股结果表
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS selected_stocks (
                id SERIAL PRIMARY KEY,
                date DATE NOT NULL,
                code VARCHAR(10) NOT NULL,
                name VARCHAR(50),
                price DECIMAL(10,2),
                change_pct DECIMAL(10,2),
                total_score DECIMAL(10,2),
                advice VARCHAR(50),
                fin_rating VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 自选股表
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS watchlist (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL,
                stock_code VARCHAR(10) NOT NULL,
                stock_name VARCHAR(50),
                alert_threshold DECIMAL(8,3) DEFAULT 3.0,
                alert_enabled BOOLEAN DEFAULT TRUE,
                added_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(user_id, stock_code)
            )
        """)
        
        # 操作日志表
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS operation_logs (
                id SERIAL PRIMARY KEY,
                log_date DATE NOT NULL,
                log_level VARCHAR(20),
                module VARCHAR(50),
                content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
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
    for page in range(1, 3):
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
    for page in range(1, 2):
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
        up_ranks = await fetch_stock_rank('0')
        down_ranks = await fetch_stock_rank('1')
        limit_up = sum(1 for s in up_ranks if s.get('change_percent', 0) >= 9.5)
        limit_down = sum(1 for s in down_ranks if s.get('change_percent', 0) <= -9.5)
        return {
            "limit_up_count": limit_up,
            "limit_down_count": limit_down,
            "sentiment": 50 + (limit_up - limit_down) // 2
        }
    except:
        return {"limit_up_count": 0, "limit_down_count": 0, "sentiment": 50}

# ========== 选股结果接口（从数据库读取）==========
@app.get("/api/stock/picks")
async def get_stock_picks():
    """获取今日选股结果"""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        engine = get_sync_engine()
        df = pd.read_sql(f"SELECT * FROM {TABLE_SELECTED} WHERE date = %s", engine, params=[today])
        
        if df.empty:
            return {"code": 200, "data": [], "has_data": False, "msg": "今日尚无选股数据"}
        
        picks = df.to_dict(orient='records')
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
        df = pd.read_sql(f"SELECT * FROM {TABLE_MARKET} WHERE date = %s", engine, params=[today])
        
        if df.empty:
            return {"code": 200, "data": None, "msg": "暂无市场数据"}
        
        row = df.iloc[0].to_dict()
        return {"code": 200, "data": row}
    except Exception as e:
        logger.error(f"获取市场数据错误: {e}")
        return {"code": 500, "message": str(e), "data": None}

# ========== 自选股接口（使用数据库）==========
@app.get("/api/watchlist")
async def get_watchlist(user_id: str):
    """获取自选股列表"""
    try:
        engine = get_sync_engine()
        df = pd.read_sql(f"""
            SELECT stock_code, stock_name, alert_threshold, added_at 
            FROM {TABLE_WATCHLIST} WHERE user_id = %s
        """, engine, params=[user_id])
        
        # 获取实时价格
        up_ranks = await fetch_stock_rank('0')
        down_ranks = await fetch_stock_rank('1')
        quote_map = {s['stock_code']: s for s in up_ranks + down_ranks}
        
        result = []
        for _, row in df.iterrows():
            code = row['stock_code']
            quote = quote_map.get(code, {})
            result.append({
                "stock_code": code,
                "stock_name": row['stock_name'] or code,
                "price": quote.get('price', 0),
                "change_percent": quote.get('change_percent', 0),
                "alert_threshold": float(row['alert_threshold']) if row['alert_threshold'] else 3.0
            })
        return result
    except Exception as e:
        logger.error(f"获取自选股错误: {e}")
        return []

@app.post("/api/watchlist")
async def add_watchlist(request: Request):
    """添加自选股"""
    try:
        body = await request.json()
        user_id = body.get('user_id')
        stock_code = body.get('stock_code')
        stock_name = body.get('stock_name', '')
        
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.execute(f"""
                INSERT INTO {TABLE_WATCHLIST} (user_id, stock_code, stock_name, added_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (user_id, stock_code) DO NOTHING
            """, user_id, stock_code, stock_name)
        
        return {"success": True, "message": "已添加到自选股"}
    except Exception as e:
        logger.error(f"添加自选股错误: {e}")
        return {"success": False, "message": str(e)}

@app.delete("/api/watchlist")
async def remove_watchlist(user_id: str, stock_code: str):
    """删除自选股"""
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.execute(f"DELETE FROM {TABLE_WATCHLIST} WHERE user_id = $1 AND stock_code = $2", 
                               user_id, stock_code)
        return {"success": True, "message": "已从自选股删除"}
    except Exception as e:
        logger.error(f"删除自选股错误: {e}")
        return {"success": False, "message": str(e)}

# ========== 个股详情接口 ==========
@app.get("/api/stock/detail")
async def get_stock_detail(stock_code: str):
    """获取个股详情"""
    try:
        up_ranks = await fetch_stock_rank('0')
        down_ranks = await fetch_stock_rank('1')
        all_stocks = {s['stock_code']: s for s in up_ranks + down_ranks}
        stock_info = all_stocks.get(stock_code, {})
        
        return {
            "stock_code": stock_code,
            "stock_name": stock_info.get('stock_name', stock_code),
            "quote": {
                "last_price": stock_info.get('price', 0),
                "change_percent": stock_info.get('change_percent', 0),
                "volume": stock_info.get('volume', 0),
                "amount": stock_info.get('amount', 0)
            }
        }
    except Exception as e:
        logger.error(f"获取个股详情错误: {e}")
        return {"stock_code": stock_code, "stock_name": stock_code, "quote": {}}

# ========== 历史选股接口 ==========
@app.get("/api/stock/picks/history")
async def get_stock_picks_history(date: str = Query(..., description="日期 YYYY-MM-DD")):
    """获取指定日期的历史选股结果"""
    try:
        engine = get_sync_engine()
        df = pd.read_sql(f"SELECT * FROM {TABLE_SELECTED} WHERE date = %s ORDER BY total_score DESC", engine, params=[date])
        
        if df.empty:
            return {"code": 200, "data": [], "has_data": False, "msg": f"{date} 无选股数据"}
        
        picks = df.to_dict(orient='records')
        return {"code": 200, "data": picks, "has_data": True}
    except Exception as e:
        logger.error(f"获取历史选股结果错误: {e}")
        return {"code": 500, "message": str(e), "data": []}

# ========== 搜索接口 ==========
@app.get("/api/stock/search")
async def search_stock(keyword: str):
    """搜索股票"""
    try:
        up_ranks = await fetch_stock_rank('0')
        down_ranks = await fetch_stock_rank('1')
        all_stocks = {s['stock_code']: s for s in up_ranks + down_ranks}
        
        results = []
        keyword_lower = keyword.lower()
        for code, info in all_stocks.items():
            if keyword_lower in code.lower() or keyword_lower in info.get('stock_name', '').lower():
                results.append({
                    "stock_code": code,
                    "stock_name": info.get('stock_name', code)
                })
        return results[:20]
    except Exception as e:
        logger.error(f"搜索错误: {e}")
        return []



# ========== 测试数据初始化接口 ==========
@app.post("/api/stock/init-test-data")
async def init_test_data():
    """初始化测试数据（仅用于开发测试）"""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        engine = get_sync_engine()
        
        # 1. 插入测试选股数据
        test_picks = pd.DataFrame([{
            'date': today,
            'code': '600519',
            'name': '贵州茅台',
            'price': 1680.00,
            'change_pct': 2.5,
            'total_score': 85,
            'advice': '持有',
            'fin_rating': '优秀'
        }, {
            'date': today,
            'code': '000858',
            'name': '五粮液',
            'price': 145.00,
            'change_pct': 3.2,
            'total_score': 82,
            'advice': '买入',
            'fin_rating': '良好'
        }, {
            'date': today,
            'code': '300750',
            'name': '宁德时代',
            'price': 220.00,
            'change_pct': 4.0,
            'total_score': 88,
            'advice': '强烈买入',
            'fin_rating': '优秀'
        }])
        
        # 删除当天旧数据
        with engine.connect() as conn:
            conn.execute(text(f"DELETE FROM {TABLE_SELECTED} WHERE date = :date"), {"date": today})
            conn.commit()
        
        # 插入新数据
        test_picks.to_sql(TABLE_SELECTED.split('.')[1], engine, schema=SCHEMA_NAME, if_exists='append', index=False)
        
        # 2. 插入测试市场数据
        test_market = pd.DataFrame([{
            'date': today,
            'market_state': '震荡市',
            'market_score': 65,
            'position_ratio': 0.5,
            'advice': '控制仓位，高抛低吸，关注科技板块',
            'trend_strength': 45,
            'volatility': 18,
            'ma_arrangement': '多头排列'
        }])
        
        with engine.connect() as conn:
            conn.execute(text(f"DELETE FROM {TABLE_MARKET} WHERE date = :date"), {"date": today})
            conn.commit()
        
        test_market.to_sql(TABLE_MARKET.split('.')[1], engine, schema=SCHEMA_NAME, if_exists='append', index=False)
        
        # 3. 插入测试热点概念数据
        test_concepts = pd.DataFrame([{
            'date': today,
            'concept_name': '人工智能',
            'change_pct': 3.5,
            'leading_stock': '科大讯飞'
        }, {
            'date': today,
            'concept_name': '新能源',
            'change_pct': 2.8,
            'leading_stock': '宁德时代'
        }, {
            'date': today,
            'concept_name': '半导体',
            'change_pct': 1.9,
            'leading_stock': '中芯国际'
        }])
        
        with engine.connect() as conn:
            conn.execute(text(f"DELETE FROM {TABLE_CONCEPTS} WHERE date = :date"), {"date": today})
            conn.commit()
        
        test_concepts.to_sql(TABLE_CONCEPTS.split('.')[1], engine, schema=SCHEMA_NAME, if_exists='append', index=False)
        
        # 4. 插入测试行业数据
        test_industries = pd.DataFrame([{
            'date': today,
            'industry_name': '白酒',
            'change_pct': 2.1,
            'leading_stock': '贵州茅台'
        }, {
            'date': today,
            'industry_name': '电池',
            'change_pct': 3.2,
            'leading_stock': '宁德时代'
        }])
        
        with engine.connect() as conn:
            conn.execute(text(f"DELETE FROM {TABLE_INDUSTRIES} WHERE date = :date"), {"date": today})
            conn.commit()
        
        test_industries.to_sql(TABLE_INDUSTRIES.split('.')[1], engine, schema=SCHEMA_NAME, if_exists='append', index=False)
        
        return {
            "code": 200, 
            "message": f"测试数据初始化完成！选股：{len(test_picks)}条，概念：{len(test_concepts)}条，行业：{len(test_industries)}条"
        }
        
    except Exception as e:
        logger.error(f"初始化测试数据失败: {e}")
        return {"code": 500, "message": str(e)}
        


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
