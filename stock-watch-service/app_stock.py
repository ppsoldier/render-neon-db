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

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("stock-watch")

# ========== 数据库配置 ==========
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://neondb_owner:npg_b1QR9lMdusev@ep-rapid-frog-ani7chkm.c-6.us-east-1.aws.neon.tech:5432/neondb?sslmode=require")

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

# ========== 九方智投签名算法 ==========
def get_real_signature(params):
    """生成九方智投接口签名"""
    secret = "sjdxfnqogbzoun13d971ckh8p"
    timestamp = str(int(time.time() * 1000))

    # 核心：按键排序 → 只拼接参数值
    sorted_keys = sorted(params.keys())
    values_str = "".join([params[k] for k in sorted_keys])

    # 签名拼接：密钥 + 值字符串 + 时间戳
    sign_str = secret + values_str + timestamp
    sign = hashlib.md5(sign_str.encode()).hexdigest()

    return sign, timestamp


async def fetch_jiufang_research(page: int = 0, page_size: int = 50):
    """从九方智投获取研报数据 - 使用正确的研报接口"""
    try:
        # 计算日期范围（最近30天）
        today = datetime.now()
        from_date = (today - timedelta(days=30)).strftime("%Y-%m-%d")
        to_date = today.strftime("%Y-%m-%d")
        
        # 请求参数
        params = {
            'pageNo': str(page),
            'pageSize': str(page_size),
            'from': from_date,
            'to': to_date,
        }
        
        # 生成签名
        sign, timestamp = get_real_signature(params)
        
        headers = {
            'referer': 'https://stock.9fzt.com/',
            'signature': sign,
            'timestamp': timestamp,
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'accept': 'application/json, text/plain, */*',
        }
        
        url = 'https://api-hq.chongnengjihua.com/news/api/1/company/report/list'
        
        response = requests.get(url=url, headers=headers, params=params, timeout=15)
        data = response.json()
        
        logger.info(f"九方智投研报接口响应码: {data.get('code')}")
        
        if data.get('code') != 1:
            logger.warning(f"研报接口返回错误: {data}")
            return []
        
        infos = data.get('data', {}).get('data', [])
        if not infos:
            logger.warning("没有获取到研报数据")
            return []
        
        reports = []
        for item in infos:
            # 解析星级（orgDescription 可能是数字）
            rating_value = item.get('orgDescription', 0)
            if rating_value and rating_value > 0:
                rating = f"{rating_value}星"
            else:
                rating = "关注"
            
            report = {
                "id": item.get('reportId', ''),
                "stock_code": item.get('symbol', ''),
                "stock_name": item.get('stockName', ''),
                "title": item.get('title', '研究报告'),
                "publisher": item.get('orgNameDisc', '九方智投'),
                "rating": rating,
                "publish_date": datetime.fromtimestamp(item.get('publishDate', 0)).strftime("%Y-%m-%d") if item.get('publishDate') else "",
                "summary": item.get('title', '点击查看详细内容'),
                "url": ""
            }
            reports.append(report)
        
        logger.info(f"获取到 {len(reports)} 条九方智投研报")
        return reports
    except Exception as e:
        logger.error(f"获取九方智投研报错误: {e}")
        return []


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
        
        # 研报表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS research_reports (
                id SERIAL PRIMARY KEY,
                stock_code VARCHAR(10) NOT NULL,
                title VARCHAR(500) NOT NULL,
                publisher VARCHAR(100),
                publish_date DATE,
                rating VARCHAR(20),
                summary TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # 插入测试股票数据
        count = await conn.fetchval("SELECT COUNT(*) FROM stocks")
        if count == 0:
            test_stocks = [
                ('000001', '平安银行', 'SZ', '银行', ['北上资金']),
                ('000858', '五粮液', 'SZ', '白酒', ['白酒概念']),
                ('300750', '宁德时代', 'SZ', '电池', ['锂电池']),
                ('600519', '贵州茅台', 'SH', '白酒', ['白酒概念']),
                ('600036', '招商银行', 'SH', '银行', ['北上资金']),
                ('002415', '海康威视', 'SZ', '计算机', ['人工智能']),
                ('601318', '中国平安', 'SH', '保险', ['保险概念']),
            ]
            for stock in test_stocks:
                await conn.execute("""
                    INSERT INTO stocks (stock_code, stock_name, market, industry, concept)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT DO NOTHING
                """, stock[0], stock[1], stock[2], stock[3], stock[4])
            logger.info("测试股票数据插入成功")
        
        logger.info("数据库初始化完成")


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
        "version": "1.0.0"
    }


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "stock-watch"}



# ========== 实时行情接口（用于首页）==========

@app.get("/api/realtime/ranks")
async def get_realtime_ranks(rank_type: str = Query("up", description="up/down")):
    """获取实时涨跌幅榜单"""
    try:
        # 从数据库获取实时行情数据
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
                LIMIT 20
            """)
            
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
    except Exception as e:
        logger.error(f"获取实时榜单错误: {e}")
        return []


@app.get("/api/realtime/industry")
async def get_realtime_industry():
    """获取实时行业板块排行"""
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.execute("SET search_path TO stock_watch")
            rows = await conn.fetch("""
                SELECT industry_code, industry_name, change_percent
                FROM industries
                WHERE change_percent IS NOT NULL
                ORDER BY change_percent DESC
                LIMIT 30
            """)
            
            return [
                {
                    "industry_code": row['industry_code'],
                    "industry_name": row['industry_name'],
                    "change_percent": float(row['change_percent']) if row['change_percent'] else 0
                }
                for row in rows
            ]
    except Exception as e:
        logger.error(f"获取行业板块错误: {e}")
        return []


@app.get("/api/realtime/concept")
async def get_realtime_concept():
    """获取实时概念板块排行"""
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.execute("SET search_path TO stock_watch")
            rows = await conn.fetch("""
                SELECT concept_code, concept_name, change_percent
                FROM concepts
                WHERE change_percent IS NOT NULL
                ORDER BY change_percent DESC
                LIMIT 30
            """)
            
            return [
                {
                    "concept_code": row['concept_code'],
                    "concept_name": row['concept_name'],
                    "change_percent": float(row['change_percent']) if row['change_percent'] else 0
                }
                for row in rows
            ]
    except Exception as e:
        logger.error(f"获取概念板块错误: {e}")
        return []


@app.get("/api/market/limit-stats")
async def get_limit_stats():
    """获取涨跌停统计"""
    try:
        # 返回模拟数据，实际可从数据库获取
        return {
            "limit_up_count": 45,
            "limit_down_count": 12,
            "sentiment": 65
        }
    except Exception as e:
        logger.error(f"获取涨跌停统计错误: {e}")
        return {
            "limit_up_count": 0,
            "limit_down_count": 0,
            "sentiment": 50
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
            "price": float(row['last_price']) if row['last_price'] else 0,
            "change_percent": float(row['change_percent']) if row['change_percent'] else 0
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
            stock = await conn.fetchrow("SELECT * FROM stocks WHERE stock_code = $1", stock_code)
            if not stock:
                raise HTTPException(status_code=404, detail="Stock not found")
            return {
                "stock_code": stock['stock_code'],
                "stock_name": stock['stock_name'],
                "industry": stock['industry'],
                "quote": {
                    "last_price": 0,
                    "change_percent": 0
                }
            }
        
        return {
            "stock_code": quote['stock_code'],
            "stock_name": quote['stock_name'],
            "industry": quote['industry'],
            "quote": {
                "last_price": float(quote['last_price']) if quote['last_price'] else 0,
                "change_percent": float(quote['change_percent']) if quote['change_percent'] else 0
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
async def add_watchlist(request: Request):
    """添加自选股"""
    try:
        body = await request.json()
        user_id = body.get('user_id')
        stock_code = body.get('stock_code')
        alert_threshold = body.get('alert_threshold', 3.0)
        
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.execute("SET search_path TO stock_watch")
            await conn.execute("""
                INSERT INTO watchlists (user_id, stock_code, alert_threshold, alert_enabled)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id, stock_code) DO UPDATE SET
                    alert_threshold = EXCLUDED.alert_threshold
            """, user_id, stock_code, alert_threshold, True)
            return {"success": True}
    except Exception as e:
        logger.error(f"添加自选股错误: {e}")
        return {"success": False, "message": str(e)}


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


@app.post("/api/watchlist/alert")
async def set_alert(request: Request):
    """设置涨跌提醒"""
    try:
        body = await request.json()
        user_id = body.get('user_id')
        stock_code = body.get('stock_code')
        threshold = body.get('threshold', 3.0)
        enabled = body.get('enabled', True)
        
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.execute("SET search_path TO stock_watch")
            await conn.execute("""
                UPDATE watchlists
                SET alert_threshold = $1, alert_enabled = $2
                WHERE user_id = $3 AND stock_code = $4
            """, threshold, enabled, user_id, stock_code)
            return {"success": True}
    except Exception as e:
        logger.error(f"设置提醒错误: {e}")
        return {"success": False, "message": str(e)}


# ========== 九方智投研报接口 ==========
@app.get("/api/research/jiufang")
async def get_jiufang_research(
    page: int = Query(1, ge=1, le=10),  # 改为从1开始
    page_size: int = Query(50, ge=1, le=100)
):
    """获取九方智投最新研报"""
    try:
        # 转换 page 为接口需要的格式（从0开始）
        api_page = page - 1
        reports = await fetch_jiufang_research(api_page, page_size)
        
        if reports:
            return {
                "code": 200,
                "message": "success",
                "data": reports,
                "total": len(reports),
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source": "九方智投"
            }
        else:
            return await get_local_research(page, page_size)
    except Exception as e:
        logger.error(f"获取九方智投研报异常: {e}")
        return await get_local_research(page, page_size)


async def get_local_research(page: int, page_size: int):
    """从本地数据库获取研报"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        
        offset = page * page_size
        rows = await conn.fetch("""
            SELECT id, title, publisher, publish_date, rating, summary, stock_code
            FROM research_reports
            ORDER BY publish_date DESC
            LIMIT $1 OFFSET $2
        """, page_size, offset)
        
        # 获取股票名称
        reports = []
        for row in rows:
            stock = await conn.fetchrow("SELECT stock_name FROM stocks WHERE stock_code = $1", row['stock_code'])
            reports.append({
                "id": row['id'],
                "stock_code": row['stock_code'],
                "stock_name": stock['stock_name'] if stock else row['stock_code'],
                "title": row['title'],
                "publisher": row['publisher'],
                "publish_date": row['publish_date'].strftime("%Y-%m-%d") if row['publish_date'] else "",
                "rating": row['rating'],
                "summary": row['summary']
            })
        
        return {
            "code": 200,
            "message": "success",
            "data": reports,
            "total": len(reports),
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "数据库"
        }


@app.get("/api/research/stock")
async def get_stock_research(stock_code: str, limit: int = Query(20, ge=1, le=50)):
    """获取个股研报"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        
        rows = await conn.fetch("""
            SELECT title, publisher, publish_date, rating, summary
            FROM research_reports
            WHERE stock_code = $1
            ORDER BY publish_date DESC
            LIMIT $2
        """, stock_code, limit)
        
        return [{
            "title": row['title'],
            "publisher": row['publisher'],
            "publish_date": row['publish_date'].strftime("%Y-%m-%d") if row['publish_date'] else None,
            "rating": row['rating'],
            "summary": row['summary']
        } for row in rows]


@app.get("/api/research/latest")
async def get_latest_research(limit: int = Query(50, ge=1, le=100)):
    """获取最新研报（本地数据库）"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        
        rows = await conn.fetch("""
            SELECT r.id, r.title, r.publisher, r.publish_date, r.rating, 
                   r.summary, r.stock_code, s.stock_name
            FROM research_reports r
            LEFT JOIN stocks s ON r.stock_code = s.stock_code
            ORDER BY r.publish_date DESC
            LIMIT $1
        """, limit)
        
        return [{
            "id": row['id'],
            "title": row['title'],
            "stock_code": row['stock_code'],
            "stock_name": row['stock_name'] or row['stock_code'],
            "publisher": row['publisher'],
            "publish_date": row['publish_date'].strftime("%Y-%m-%d") if row['publish_date'] else None,
            "rating": row['rating'],
            "summary": row['summary']
        } for row in rows]


# ========== 定时任务 ==========
scheduler = BackgroundScheduler()

def scheduled_data_fetch():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.close()

scheduler.add_job(scheduled_data_fetch, 'interval', minutes=10)
scheduler.start()


# ========== 启动事件 ==========
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Stock Watch System...")
    await init_database()
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
