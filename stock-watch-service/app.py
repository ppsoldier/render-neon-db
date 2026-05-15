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
                limit_down_count INT DEFAULT 0
            )
        """)
        
        # 插入测试数据
        count = await conn.fetchval("SELECT COUNT(*) FROM stocks")
        if count == 0:
            test_stocks = [
                ('000001', '平安银行', 'SZ', '银行', ['北上资金']),
                ('000002', '万科A', 'SZ', '房地产', ['租售同权']),
                ('000858', '五粮液', 'SZ', '白酒', ['白酒概念']),
                ('002415', '海康威视', 'SZ', '计算机', ['人工智能']),
                ('300750', '宁德时代', 'SZ', '电池', ['锂电池', '新能源车']),
                ('600519', '贵州茅台', 'SH', '白酒', ['白酒概念']),
                ('600036', '招商银行', 'SH', '银行', ['北上资金']),
                ('601318', '中国平安', 'SH', '保险', ['保险概念']),
            ]
            for stock in test_stocks:
                await conn.execute("""
                    INSERT INTO stocks (stock_code, stock_name, market, industry, concept)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT DO NOTHING
                """, stock[0], stock[1], stock[2], stock[3], stock[4])
            logger.info("Test stocks inserted")
        
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
                    logger.error(f"Error insert {row.get('代码', 'unknown')}: {e}")
            
            logger.info(f"Updated {count} stocks")
        return True
    except Exception as e:
        logger.error(f"Fetch error: {e}")
        return False

async def fetch_industry_ranking():
    """采集行业排行"""
    try:
        df = ak.stock_board_industry_spot_em()
        if df is None or df.empty:
            return False
        
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.execute("SET search_path TO stock_watch")
            for _, row in df.head(20).iterrows():
                await conn.execute("""
                    INSERT INTO industries (industry_code, industry_name, change_percent, update_time)
                    VALUES ($1, $2, $3, NOW())
                    ON CONFLICT (industry_code) DO UPDATE SET
                        industry_name = EXCLUDED.industry_name,
                        change_percent = EXCLUDED.change_percent,
                        update_time = NOW()
                """, str(row['板块代码']), str(row['板块名称']), float(row['涨跌幅']))
            logger.info(f"Updated {len(df.head(20))} industries")
        return True
    except Exception as e:
        logger.error(f"Industry error: {e}")
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

# ========== 根路径 ==========
@app.get("/")
async def root():
    return {
        "service": "股票看盘系统",
        "status": "running",
        "version": "1.0.0",
        "database": "Neon PostgreSQL",
        "endpoints": {
            "health": "/health",
            "ranks": "/api/market/ranks?rank_type=up",
            "industry": "/api/market/industry",
            "concept": "/api/market/concept",
            "stock_detail": "/api/stock/detail?stock_code=000001",
            "search": "/api/stock/search?keyword=平安",
            "watchlist": "/api/watchlist?user_id=test_user",
            "limit_stats": "/api/market/limit-stats"
        }
    }

@app.get("/health")
async def health_check():
    """健康检查"""
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {e}"
    
    return {
        "status": "healthy",
        "service": "stock-watch",
        "database": db_status,
        "timestamp": datetime.now().isoformat()
    }

# ========== 行情接口 ==========
@app.get("/api/market/ranks")
async def get_market_ranks(rank_type: str = Query("up"), limit: int = Query(20)):
    """获取涨跌幅榜单"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        order = "DESC" if rank_type == "up" else "ASC"
        rows = await conn.fetch(f"""
            SELECT r.stock_code, s.stock_name, r.last_price, r.change_percent, r.volume, r.amount
            FROM realtime_quotes r
            JOIN stocks s ON r.stock_code = s.stock_code
            WHERE r.change_percent IS NOT NULL
            ORDER BY r.change_percent {order}
            LIMIT $1
        """, limit)
        
        return [{
            "stock_code": row['stock_code'],
            "stock_name": row['stock_name'],
            "price": float(row['last_price']) if row['last_price'] else 0,
            "change_percent": float(row['change_percent']) if row['change_percent'] else 0,
            "volume": row['volume'],
            "amount": row['amount']
        } for row in rows]

@app.get("/api/market/industry")
async def get_industry_ranking(rank_type: str = Query("up")):
    """获取行业板块排行"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        order = "DESC" if rank_type == "up" else "ASC"
        rows = await conn.fetch(f"""
            SELECT industry_code, industry_name, change_percent
            FROM industries
            WHERE change_percent IS NOT NULL
            ORDER BY change_percent {order}
        """)
        
        return [{
            "industry_code": row['industry_code'],
            "industry_name": row['industry_name'],
            "change_percent": float(row['change_percent']) if row['change_percent'] else 0
        } for row in rows]

@app.get("/api/market/concept")
async def get_concept_ranking(rank_type: str = Query("up")):
    """获取概念板块排行"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        order = "DESC" if rank_type == "up" else "ASC"
        rows = await conn.fetch(f"""
            SELECT concept_code, concept_name, change_percent
            FROM concepts
            WHERE change_percent IS NOT NULL
            ORDER BY change_percent {order}
        """)
        
        return [{
            "concept_code": row['concept_code'],
            "concept_name": row['concept_name'],
            "change_percent": float(row['change_percent']) if row['change_percent'] else 0
        } for row in rows]

@app.get("/api/stock/detail")
async def get_stock_detail(stock_code: str):
    """获取个股详情"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        quote = await conn.fetchrow("""
            SELECT r.*, s.stock_name, s.industry, s.concept
            FROM realtime_quotes r
            JOIN stocks s ON r.stock_code = s.stock_code
            WHERE r.stock_code = $1
        """, stock_code)
        
        if not quote:
            stock = await conn.fetchrow("SELECT * FROM stocks WHERE stock_code = $1", stock_code)
            if not stock:
                raise HTTPException(status_code=404, detail="Stock not found")
            return {
                "stock_code": stock['stock_code'],
                "stock_name": stock['stock_name'],
                "industry": stock['industry'],
                "concept": stock['concept'],
                "quote": {
                    "last_price": 0,
                    "change_percent": 0,
                    "volume": 0,
                    "amount": 0
                }
            }
        
        return {
            "stock_code": quote['stock_code'],
            "stock_name": quote['stock_name'],
            "industry": quote['industry'],
            "concept": quote['concept'],
            "quote": {
                "last_price": float(quote['last_price']) if quote['last_price'] else 0,
                "change_percent": float(quote['change_percent']) if quote['change_percent'] else 0,
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

@app.get("/api/market/limit-stats")
async def get_limit_stats():
    """获取涨跌停统计"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        today = await conn.fetchrow("""
            SELECT limit_up_count, limit_down_count
            FROM limit_stats
            WHERE trade_date = CURRENT_DATE
        """)
        
        sentiment = 50
        if today and today['limit_up_count'] + today['limit_down_count'] > 0:
            total = today['limit_up_count'] + today['limit_down_count']
            sentiment = int(today['limit_up_count'] / total * 100)
        
        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "limit_up_count": today['limit_up_count'] if today else 0,
            "limit_down_count": today['limit_down_count'] if today else 0,
            "sentiment": min(100, max(0, sentiment))
        }

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
        stock = await conn.fetchrow("SELECT stock_code FROM stocks WHERE stock_code = $1", item.stock_code)
        if not stock:
            raise HTTPException(status_code=404, detail="Stock not found")
        
        await conn.execute("""
            INSERT INTO watchlists (user_id, stock_code, alert_threshold, alert_enabled)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, stock_code) DO UPDATE SET
                alert_threshold = EXCLUDED.alert_threshold
        """, user_id, item.stock_code, item.alert_threshold, True)
        
        return {"success": True, "message": "Added to watchlist"}

@app.delete("/api/watchlist")
async def remove_watchlist(user_id: str, stock_code: str):
    """删除自选股"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        await conn.execute("""
            DELETE FROM watchlists WHERE user_id = $1 AND stock_code = $2
        """, user_id, stock_code)
        return {"success": True, "message": "Removed from watchlist"}

@app.post("/api/watchlist/alert")
async def set_alert(user_id: str, alert: AlertSetting):
    """设置涨跌提醒"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        await conn.execute("""
            UPDATE watchlists
            SET alert_threshold = $1, alert_enabled = $2
            WHERE user_id = $3 AND stock_code = $4
        """, alert.threshold, alert.enabled, user_id, alert.stock_code)
        return {"success": True, "message": "Alert set successfully"}

# ========== 定时任务 ==========
scheduler = BackgroundScheduler()

def scheduled_data_fetch():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    now = datetime.now()
    hour = now.hour
    if 9 <= hour <= 15:
        loop.run_until_complete(fetch_and_update_quotes())
    if hour == 9 and now.minute == 30:
        loop.run_until_complete(fetch_industry_ranking())
    loop.close()

scheduler.add_job(scheduled_data_fetch, 'interval', minutes=10)
scheduler.start()

# ========== 启动事件 ==========
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Stock Watch System...")
    await init_database()
    await fetch_and_update_quotes()
    await fetch_industry_ranking()
    logger.info("Stock Watch System ready!")
    yield
    if db_pool:
        await db_pool.close()
    scheduler.shutdown()

app.router.lifespan_context = lifespan

# ========== 主入口 ==========
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8081))
    uvicorn.run(app, host="0.0.0.0", port=port)
