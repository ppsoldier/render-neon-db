import os
import asyncio
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import asyncpg
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("stock-watch")

# ========== 数据库配置（使用您的Neon配置）==========
DATABASE_URL = "postgresql://neondb_owner:npg_b1QR9lMdusev@ep-rapid-frog-ani7chkm.c-6.us-east-1.aws.neon.tech:5432/neondb?sslmode=require"

# ========== 数据模型 ==========
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
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # 实时行情表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS realtime_quotes (
                stock_code VARCHAR(10) PRIMARY KEY,
                last_price DECIMAL(10,3),
                change_percent DECIMAL(8,3),
                volume BIGINT,
                amount DECIMAL(16,2),
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
                PRIMARY KEY (stock_code, trade_date)
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
        
        # 插入测试数据
        count = await conn.fetchval("SELECT COUNT(*) FROM stocks")
        if count == 0:
            test_stocks = [
                ('000001', '平安银行', 'SZ', '银行', ['北上资金']),
                ('000858', '五粮液', 'SZ', '白酒', ['白酒']),
                ('300750', '宁德时代', 'SZ', '电池', ['锂电池']),
                ('600519', '贵州茅台', 'SH', '白酒', ['白酒']),
            ]
            for stock in test_stocks:
                await conn.execute("""
                    INSERT INTO stocks (stock_code, stock_name, market, industry, concept)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT DO NOTHING
                """, stock[0], stock[1], stock[2], stock[3], stock[4])
        
        logger.info("Database initialized on Neon")

# ========== 数据采集服务 ==========
import akshare as ak

async def fetch_and_update_quotes():
    """采集实时行情"""
    try:
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return False
        
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.execute("SET search_path TO stock_watch")
            count = 0
            for _, row in df.head(50).iterrows():
                try:
                    await conn.execute("""
                        INSERT INTO realtime_quotes 
                        (stock_code, last_price, change_percent, volume, amount, update_time)
                        VALUES ($1, $2, $3, $4, $5, NOW())
                        ON CONFLICT (stock_code) DO UPDATE SET
                            last_price = EXCLUDED.last_price,
                            change_percent = EXCLUDED.change_percent,
                            volume = EXCLUDED.volume,
                            amount = EXCLUDED.amount,
                            update_time = NOW()
                    """, 
                        str(row['代码']), 
                        float(row['最新价']) if row['最新价'] else 0,
                        float(row['涨跌幅']) if row['涨跌幅'] else 0,
                        int(row['成交量']) if row['成交量'] else 0,
                        float(row['成交额']) if row['成交额'] else 0
                    )
                    count += 1
                except Exception as e:
                    logger.error(f"Error: {e}")
            
            logger.info(f"Updated {count} stocks")
        return True
    except Exception as e:
        logger.error(f"Fetch error: {e}")
        return False

# ========== API 接口 ==========
app = FastAPI(title="股票看盘系统", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"service": "股票看盘系统", "status": "running"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "stock-watch"}

@app.get("/api/market/ranks")
async def get_market_ranks(rank_type: str = Query("up"), limit: int = Query(20)):
    """获取涨跌幅榜单"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        order = "DESC" if rank_type == "up" else "ASC"
        rows = await conn.fetch(f"""
            SELECT r.stock_code, s.stock_name, r.last_price, r.change_percent
            FROM realtime_quotes r
            JOIN stocks s ON r.stock_code = s.stock_code
            WHERE r.change_percent IS NOT NULL
            ORDER BY r.change_percent {order}
            LIMIT $1
        """, limit)
        
        return [{
            "stock_code": row['stock_code'],
            "stock_name": row['stock_name'],
            "price": float(row['last_price']),
            "change_percent": float(row['change_percent'])
        } for row in rows]

@app.get("/api/stock/detail")
async def get_stock_detail(stock_code: str):
    """获取个股详情"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        quote = await conn.fetchrow("""
            SELECT r.*, s.stock_name, s.industry
            FROM realtime_quotes r
            JOIN stocks s ON r.stock_code = s.stock_code
            WHERE r.stock_code = $1
        """, stock_code)
        
        if not quote:
            raise HTTPException(status_code=404, detail="Stock not found")
        
        return {
            "stock_code": quote['stock_code'],
            "stock_name": quote['stock_name'],
            "industry": quote['industry'],
            "quote": {
                "last_price": float(quote['last_price']),
                "change_percent": float(quote['change_percent']),
                "volume": quote['volume'],
                "amount": quote['amount']
            }
        }

@app.get("/api/stock/search")
async def search_stock(keyword: str):
    """搜索股票"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        rows = await conn.fetch("""
            SELECT stock_code, stock_name, market, industry
            FROM stocks
            WHERE stock_code LIKE $1 OR stock_name LIKE $1
            LIMIT 20
        """, f"%{keyword}%")
        
        return [{
            "stock_code": row['stock_code'],
            "stock_name": row['stock_name'],
            "market": row['market'],
            "industry": row['industry']
        } for row in rows]

# ========== 自选股接口 ==========
@app.get("/api/watchlist")
async def get_watchlist(user_id: str):
    """获取自选股列表"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        rows = await conn.fetch("""
            SELECT w.stock_code, s.stock_name, w.alert_threshold,
                   r.last_price, r.change_percent
            FROM watchlists w
            JOIN stocks s ON w.stock_code = s.stock_code
            LEFT JOIN realtime_quotes r ON w.stock_code = r.stock_code
            WHERE w.user_id = $1
        """, user_id)
        
        return [{
            "stock_code": row['stock_code'],
            "stock_name": row['stock_name'],
            "price": float(row['last_price']) if row['last_price'] else 0,
            "change_percent": float(row['change_percent']) if row['change_percent'] else 0,
            "alert_threshold": float(row['alert_threshold'])
        } for row in rows]

@app.post("/api/watchlist")
async def add_watchlist(user_id: str, item: WatchlistItem):
    """添加自选股"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        await conn.execute("""
            INSERT INTO watchlists (user_id, stock_code, alert_threshold, alert_enabled)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, stock_code) DO UPDATE SET
                alert_threshold = EXCLUDED.alert_threshold
        """, user_id, item.stock_code, item.alert_threshold, True)
        
        return {"success": True}

@app.delete("/api/watchlist")
async def remove_watchlist(user_id: str, stock_code: str):
    """删除自选股"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        await conn.execute("""
            DELETE FROM watchlists WHERE user_id = $1 AND stock_code = $2
        """, user_id, stock_code)
        return {"success": True}

# ========== 定时任务 ==========
scheduler = BackgroundScheduler()

def scheduled_data_fetch():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(fetch_and_update_quotes())
    loop.close()

scheduler.add_job(scheduled_data_fetch, 'interval', minutes=10, id='data_fetch')
scheduler.start()

# ========== 启动事件 ==========
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Stock Watch System...")
    await init_database()
    await fetch_and_update_quotes()
    logger.info("Ready")
    yield
    if db_pool:
        await db_pool.close()
    scheduler.shutdown()

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8081))
    uvicorn.run(app, host="0.0.0.0", port=port)
