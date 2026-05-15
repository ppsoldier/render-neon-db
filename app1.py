import os
import asyncio
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import asyncpg
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("stock-watch")

# ========== 配置 ==========
DATABASE_URL = os.environ.get("STOCK_DATABASE_URL") or os.environ.get("DATABASE_URL")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
JWT_SECRET = os.environ.get("JWT_SECRET", "stock_watch_secret_key_2026")

# ========== 数据模型 ==========
class Stock(BaseModel):
    stock_code: str
    stock_name: str
    market: str
    industry: Optional[str] = None
    concept: Optional[List[str]] = None

class RealtimeQuote(BaseModel):
    stock_code: str
    last_price: float
    change_percent: float
    volume: int
    amount: float
    update_time: datetime

class WatchlistItem(BaseModel):
    stock_code: str
    alert_threshold: Optional[float] = 3.0

class AlertSetting(BaseModel):
    stock_code: str
    threshold: float
    enabled: bool = True

# ========== 数据库连接池 ==========
db_pool = None

async def get_db():
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    return db_pool

# ========== 初始化数据库表 ==========
async def init_database():
    pool = await get_db()
    async with pool.acquire() as conn:
        # 创建 schema
        await conn.execute("CREATE SCHEMA IF NOT EXISTS stock_watch")
        await conn.execute("SET search_path TO stock_watch")
        
        # 股票基本信息表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS stocks (
                stock_code VARCHAR(10) PRIMARY KEY,
                stock_name VARCHAR(50) NOT NULL,
                market VARCHAR(10) NOT NULL,
                industry VARCHAR(50),
                concept TEXT[],
                list_date DATE,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # 实时行情表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS realtime_quotes (
                stock_code VARCHAR(10) PRIMARY KEY,
                last_price DECIMAL(10,3),
                open DECIMAL(10,3),
                high DECIMAL(10,3),
                low DECIMAL(10,3),
                volume BIGINT,
                amount DECIMAL(16,2),
                change_percent DECIMAL(8,3),
                update_time TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # 日K线数据表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_quotes (
                stock_code VARCHAR(10) NOT NULL,
                trade_date DATE NOT NULL,
                open DECIMAL(10,3),
                high DECIMAL(10,3),
                low DECIMAL(10,3),
                close DECIMAL(10,3),
                volume BIGINT,
                amount DECIMAL(16,2),
                change_percent DECIMAL(8,3),
                PRIMARY KEY (stock_code, trade_date)
            )
        """)
        
        # 行业板块表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS industries (
                industry_code VARCHAR(20) PRIMARY KEY,
                industry_name VARCHAR(100) NOT NULL,
                change_percent DECIMAL(8,3),
                update_time TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # 概念板块表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS concepts (
                concept_code VARCHAR(20) PRIMARY KEY,
                concept_name VARCHAR(100) NOT NULL,
                change_percent DECIMAL(8,3),
                update_time TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # 板块成分股
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS sector_stocks (
                sector_code VARCHAR(20) NOT NULL,
                sector_type VARCHAR(10) NOT NULL,
                stock_code VARCHAR(10) NOT NULL,
                PRIMARY KEY (sector_code, stock_code)
            )
        """)
        
        # 自选股表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlists (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL,
                stock_code VARCHAR(10) NOT NULL,
                alert_threshold DECIMAL(8,3) DEFAULT 3.0,
                alert_enabled BOOLEAN DEFAULT TRUE,
                added_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(user_id, stock_code)
            )
        """)
        
        # 涨跌停统计表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS limit_stats (
                trade_date DATE PRIMARY KEY,
                limit_up_count INT DEFAULT 0,
                limit_down_count INT DEFAULT 0,
                continuous_limit_up_count INT DEFAULT 0
            )
        """)
        
        # 研报表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS research_reports (
                id SERIAL PRIMARY KEY,
                stock_code VARCHAR(10) NOT NULL,
                title VARCHAR(200) NOT NULL,
                publisher VARCHAR(50),
                publish_date DATE,
                rating VARCHAR(20),
                summary TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # 提醒记录表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_records (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL,
                stock_code VARCHAR(10) NOT NULL,
                alert_type VARCHAR(20),
                price DECIMAL(10,3),
                change_percent DECIMAL(8,3),
                triggered_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        logger.info("Database tables initialized")
        
        # 创建索引
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_stock_date ON daily_quotes(stock_code, trade_date)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlists(user_id)")

# ========== 数据采集服务 ==========
import akshare as ak
import pandas as pd

async def fetch_realtime_quotes():
    """采集实时行情"""
    try:
        # 获取A股实时行情
        df = ak.stock_zh_a_spot_em()
        
        pool = await get_db()
        async with pool.acquire() as conn:
            for _, row in df.iterrows():
                await conn.execute("""
                    INSERT INTO realtime_quotes 
                    (stock_code, last_price, open, high, low, volume, amount, change_percent, update_time)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
                    ON CONFLICT (stock_code) DO UPDATE SET
                        last_price = EXCLUDED.last_price,
                        open = EXCLUDED.open,
                        high = EXCLUDED.high,
                        low = EXCLUDED.low,
                        volume = EXCLUDED.volume,
                        amount = EXCLUDED.amount,
                        change_percent = EXCLUDED.change_percent,
                        update_time = NOW()
                """, 
                    row['代码'], row['名称'], row['最新价'], row['今开'], 
                    row['最高'], row['最低'], row['成交量'], row['成交额'], row['涨跌幅'])
        
        logger.info(f"Realtime quotes fetched: {len(df)} stocks")
        return True
    except Exception as e:
        logger.error(f"Fetch realtime quotes error: {e}")
        return False

async def fetch_limit_stats():
    """采集涨跌停统计"""
    try:
        # 涨停股池
        limit_up_df = ak.stock_zt_pool_em(date=datetime.now().strftime('%Y%m%d'))
        # 跌停股池
        limit_down_df = ak.stock_dt_pool_em(date=datetime.now().strftime('%Y%m%d'))
        
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO limit_stats (trade_date, limit_up_count, limit_down_count)
                VALUES (CURRENT_DATE, $1, $2)
                ON CONFLICT (trade_date) DO UPDATE SET
                    limit_up_count = EXCLUDED.limit_up_count,
                    limit_down_count = EXCLUDED.limit_down_count
            """, len(limit_up_df), len(limit_down_df))
        
        logger.info(f"Limit stats: up={len(limit_up_df)}, down={len(limit_down_df)}")
        return True
    except Exception as e:
        logger.error(f"Fetch limit stats error: {e}")
        return False

async def fetch_industry_ranking():
    """采集行业板块排行"""
    try:
        df = ak.stock_board_industry_spot_em()
        
        pool = await get_db()
        async with pool.acquire() as conn:
            for _, row in df.iterrows():
                await conn.execute("""
                    INSERT INTO industries (industry_code, industry_name, change_percent, update_time)
                    VALUES ($1, $2, $3, NOW())
                    ON CONFLICT (industry_code) DO UPDATE SET
                        industry_name = EXCLUDED.industry_name,
                        change_percent = EXCLUDED.change_percent,
                        update_time = NOW()
                """, row['板块代码'], row['板块名称'], row['涨跌幅'])
        
        logger.info(f"Industry ranking fetched: {len(df)} industries")
        return True
    except Exception as e:
        logger.error(f"Fetch industry ranking error: {e}")
        return False

# ========== API 接口 ==========

app = FastAPI(title="股票看盘系统 API", version="1.0.0")

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== 行情接口 ==========

@app.get("/api/market/ranks")
async def get_market_ranks(rank_type: str = Query("up", description="up/down"), limit: int = Query(20, ge=1, le=100)):
    """获取涨跌幅榜单"""
    pool = await get_db()
    async with pool.acquire() as conn:
        order = "DESC" if rank_type == "up" else "ASC"
        rows = await conn.fetch(f"""
            SELECT r.stock_code, s.stock_name, r.last_price, r.change_percent, r.volume, r.amount
            FROM stock_watch.realtime_quotes r
            JOIN stock_watch.stocks s ON r.stock_code = s.stock_code
            WHERE r.change_percent IS NOT NULL
            ORDER BY r.change_percent {order}
            LIMIT $1
        """, limit)
        
        return [
            {
                "stock_code": row['stock_code'],
                "stock_name": row['stock_name'],
                "price": float(row['last_price']) if row['last_price'] else 0,
                "change_percent": float(row['change_percent']) if row['change_percent'] else 0,
                "volume": row['volume'],
                "amount": row['amount']
            }
            for row in rows
        ]

@app.get("/api/market/industry")
async def get_industry_ranking(rank_type: str = Query("up", description="up/down")):
    """获取行业板块排行"""
    pool = await get_db()
    async with pool.acquire() as conn:
        order = "DESC" if rank_type == "up" else "ASC"
        rows = await conn.fetch(f"""
            SELECT industry_code, industry_name, change_percent, update_time
            FROM stock_watch.industries
            WHERE change_percent IS NOT NULL
            ORDER BY change_percent {order}
        """)
        
        return [
            {
                "industry_code": row['industry_code'],
                "industry_name": row['industry_name'],
                "change_percent": float(row['change_percent'])
            }
            for row in rows
        ]

@app.get("/api/market/concept")
async def get_concept_ranking(rank_type: str = Query("up", description="up/down")):
    """获取概念板块排行"""
    pool = await get_db()
    async with pool.acquire() as conn:
        order = "DESC" if rank_type == "up" else "ASC"
        rows = await conn.fetch(f"""
            SELECT concept_code, concept_name, change_percent, update_time
            FROM stock_watch.concepts
            WHERE change_percent IS NOT NULL
            ORDER BY change_percent {order}
        """)
        
        return [
            {
                "concept_code": row['concept_code'],
                "concept_name": row['concept_name'],
                "change_percent": float(row['change_percent'])
            }
            for row in rows
        ]

@app.get("/api/market/sector/stocks")
async def get_sector_stocks(sector_code: str, sector_type: str = Query("industry", description="industry/concept")):
    """获取板块成分股"""
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT ss.stock_code, s.stock_name, r.last_price, r.change_percent
            FROM stock_watch.sector_stocks ss
            JOIN stock_watch.stocks s ON ss.stock_code = s.stock_code
            JOIN stock_watch.realtime_quotes r ON ss.stock_code = r.stock_code
            WHERE ss.sector_code = $1 AND ss.sector_type = $2
        """, sector_code, sector_type)
        
        return [
            {
                "stock_code": row['stock_code'],
                "stock_name": row['stock_name'],
                "price": float(row['last_price']) if row['last_price'] else 0,
                "change_percent": float(row['change_percent']) if row['change_percent'] else 0
            }
            for row in rows
        ]

@app.get("/api/stock/detail")
async def get_stock_detail(stock_code: str):
    """获取个股详情"""
    pool = await get_db()
    async with pool.acquire() as conn:
        # 获取实时行情
        quote = await conn.fetchrow("""
            SELECT r.*, s.stock_name, s.industry, s.concept
            FROM stock_watch.realtime_quotes r
            JOIN stock_watch.stocks s ON r.stock_code = s.stock_code
            WHERE r.stock_code = $1
        """, stock_code)
        
        if not quote:
            raise HTTPException(status_code=404, detail="Stock not found")
        
        # 获取近期K线
        klines = await conn.fetch("""
            SELECT trade_date, open, high, low, close, volume, amount, change_percent
            FROM stock_watch.daily_quotes
            WHERE stock_code = $1
            ORDER BY trade_date DESC
            LIMIT 30
        """, stock_code)
        
        return {
            "stock_code": quote['stock_code'],
            "stock_name": quote['stock_name'],
            "industry": quote['industry'],
            "concept": quote['concept'],
            "quote": {
                "last_price": float(quote['last_price']) if quote['last_price'] else 0,
                "open": float(quote['open']) if quote['open'] else 0,
                "high": float(quote['high']) if quote['high'] else 0,
                "low": float(quote['low']) if quote['low'] else 0,
                "volume": quote['volume'],
                "amount": quote['amount'],
                "change_percent": float(quote['change_percent']) if quote['change_percent'] else 0
            },
            "klines": [
                {
                    "date": k['trade_date'].strftime("%Y-%m-%d"),
                    "open": float(k['open']),
                    "high": float(k['high']),
                    "low": float(k['low']),
                    "close": float(k['close']),
                    "volume": k['volume'],
                    "change_percent": float(k['change_percent'])
                }
                for k in klines
            ]
        }

@app.get("/api/stock/kline")
async def get_stock_kline(
    stock_code: str, 
    period: str = Query("daily", description="daily/weekly/monthly"),
    days: int = Query(120, ge=1, le=365)
):
    """获取K线图数据"""
    pool = await get_db()
    async with pool.acquire() as conn:
        if period == "daily":
            rows = await conn.fetch("""
                SELECT trade_date, open, high, low, close, volume, amount
                FROM stock_watch.daily_quotes
                WHERE stock_code = $1
                ORDER BY trade_date DESC
                LIMIT $2
            """, stock_code, days)
        else:
            # 周K/月K 需要聚合，简化处理
            rows = await conn.fetch("""
                SELECT trade_date, open, high, low, close, volume, amount
                FROM stock_watch.daily_quotes
                WHERE stock_code = $1
                ORDER BY trade_date DESC
                LIMIT $2
            """, stock_code, days)
        
        return {
            "stock_code": stock_code,
            "period": period,
            "data": [
                {
                    "date": row['trade_date'].strftime("%Y-%m-%d"),
                    "open": float(row['open']),
                    "high": float(row['high']),
                    "low": float(row['low']),
                    "close": float(row['close']),
                    "volume": row['volume']
                }
                for row in reversed(rows)
            ]
        }

@app.get("/api/stock/search")
async def search_stock(keyword: str):
    """搜索股票"""
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT stock_code, stock_name, market, industry
            FROM stock_watch.stocks
            WHERE stock_code LIKE $1 OR stock_name LIKE $1
            LIMIT 20
        """, f"%{keyword}%")
        
        return [
            {
                "stock_code": row['stock_code'],
                "stock_name": row['stock_name'],
                "market": row['market'],
                "industry": row['industry']
            }
            for row in rows
        ]

@app.get("/api/market/limit-stats")
async def get_limit_stats():
    """获取涨跌停统计"""
    pool = await get_db()
    async with pool.acquire() as conn:
        # 今日统计
        today = await conn.fetchrow("""
            SELECT limit_up_count, limit_down_count
            FROM stock_watch.limit_stats
            WHERE trade_date = CURRENT_DATE
        """)
        
        # 市场情绪计算
        sentiment = 50  # 默认中性
        if today:
            total = today['limit_up_count'] + today['limit_down_count']
            if total > 0:
                sentiment = int(today['limit_up_count'] / total * 100)
        
        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "limit_up_count": today['limit_up_count'] if today else 0,
            "limit_down_count": today['limit_down_count'] if today else 0,
            "sentiment": min(100, max(0, sentiment))
        }

# ========== 自选股接口 ==========

@app.get("/api/watchlist")
async def get_watchlist(user_id: str = Query(..., description="用户标识")):
    """获取自选股列表"""
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT w.stock_code, s.stock_name, w.alert_threshold, w.alert_enabled,
                   r.last_price, r.change_percent
            FROM stock_watch.watchlists w
            JOIN stock_watch.stocks s ON w.stock_code = s.stock_code
            LEFT JOIN stock_watch.realtime_quotes r ON w.stock_code = r.stock_code
            WHERE w.user_id = $1
            ORDER BY w.added_at
        """, user_id)
        
        return [
            {
                "stock_code": row['stock_code'],
                "stock_name": row['stock_name'],
                "price": float(row['last_price']) if row['last_price'] else 0,
                "change_percent": float(row['change_percent']) if row['change_percent'] else 0,
                "alert_threshold": float(row['alert_threshold']),
                "alert_enabled": row['alert_enabled']
            }
            for row in rows
        ]

@app.post("/api/watchlist")
async def add_watchlist(user_id: str, item: WatchlistItem):
    """添加自选股"""
    pool = await get_db()
    async with pool.acquire() as conn:
        # 检查股票是否存在
        stock = await conn.fetchrow("SELECT stock_code FROM stock_watch.stocks WHERE stock_code = $1", item.stock_code)
        if not stock:
            raise HTTPException(status_code=404, detail="Stock not found")
        
        await conn.execute("""
            INSERT INTO stock_watch.watchlists (user_id, stock_code, alert_threshold, alert_enabled)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, stock_code) DO UPDATE SET
                alert_threshold = EXCLUDED.alert_threshold,
                alert_enabled = EXCLUDED.alert_enabled
        """, user_id, item.stock_code, item.alert_threshold, True)
        
        return {"success": True, "message": "Added to watchlist"}

@app.delete("/api/watchlist")
async def remove_watchlist(user_id: str, stock_code: str):
    """删除自选股"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("""
            DELETE FROM stock_watch.watchlists
            WHERE user_id = $1 AND stock_code = $2
        """, user_id, stock_code)
        
        return {"success": True, "message": "Removed from watchlist"}

@app.post("/api/watchlist/alert")
async def set_alert(user_id: str, alert: AlertSetting):
    """设置涨跌提醒"""
    pool = await get_db()
    async with pool.acquire() as conn:
        # 更新提醒阈值
        await conn.execute("""
            UPDATE stock_watch.watchlists
            SET alert_threshold = $1, alert_enabled = $2
            WHERE user_id = $3 AND stock_code = $4
        """, alert.threshold, alert.enabled, user_id, alert.stock_code)
        
        return {"success": True, "message": "Alert set successfully"}

# ========== 研报接口 ==========

@app.get("/api/research/stock")
async def get_stock_research(stock_code: str, limit: int = Query(20, ge=1, le=50)):
    """获取个股研报"""
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT title, publisher, publish_date, rating, summary
            FROM stock_watch.research_reports
            WHERE stock_code = $1
            ORDER BY publish_date DESC
            LIMIT $2
        """, stock_code, limit)
        
        return [
            {
                "title": row['title'],
                "publisher": row['publisher'],
                "publish_date": row['publish_date'].strftime("%Y-%m-%d") if row['publish_date'] else None,
                "rating": row['rating'],
                "summary": row['summary']
            }
            for row in rows
        ]

@app.get("/api/research/latest")
async def get_latest_research(limit: int = Query(20, ge=1, le=50)):
    """获取最新研报"""
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT r.title, r.publisher, r.publish_date, r.rating, r.stock_code, s.stock_name
            FROM stock_watch.research_reports r
            JOIN stock_watch.stocks s ON r.stock_code = s.stock_code
            ORDER BY r.publish_date DESC
            LIMIT $1
        """, limit)
        
        return [
            {
                "stock_code": row['stock_code'],
                "stock_name": row['stock_name'],
                "title": row['title'],
                "publisher": row['publisher'],
                "publish_date": row['publish_date'].strftime("%Y-%m-%d") if row['publish_date'] else None,
                "rating": row['rating']
            }
            for row in rows
        ]

# ========== 健康检查 ==========
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "stock-watch-system",
        "timestamp": datetime.now().isoformat()
    }

# ========== 定时任务 ==========
scheduler = BackgroundScheduler()

def scheduled_data_fetch():
    """定时采集数据"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # 交易时段高频采集，非交易时段降低频率
    now = datetime.now()
    is_trading_time = 9 <= now.hour < 15
    
    if is_trading_time:
        loop.run_until_complete(fetch_realtime_quotes())
    
    # 以下任务每天执行一次
    if now.hour == 15 and now.minute < 30:
        loop.run_until_complete(fetch_limit_stats())
        loop.run_until_complete(fetch_industry_ranking())
    
    loop.close()

# 启动定时任务
scheduler.add_job(scheduled_data_fetch, 'interval', minutes=3, id='data_fetch')
scheduler.start()

# ========== 启动事件 ==========
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化
    await init_database()
    logger.info("Stock Watch System started")
    yield
    # 关闭时清理
    if db_pool:
        await db_pool.close()
    scheduler.shutdown()

app.router.lifespan_context = lifespan

# ========== 主入口 ==========
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8081))
    uvicorn.run(app, host="0.0.0.0", port=port)
