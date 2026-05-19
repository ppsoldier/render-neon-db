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

# 获取股票涨幅榜 signature
def generate_signature(listed_sector, sort_field, sort_type, timestamp, page):
    base_string = f"sjdxfnqogbzoun13d971ckh8p{listed_sector}{page}20{sort_field}{sort_type}{timestamp}"
    return hashlib.md5(base_string.encode()).hexdigest()

# 获取股票行业板块排行榜 signature
def get_sector_signature(time_stamp):
    base_string = f"sjdxfnqogbzoun13d971ckh8p{time_stamp}"
    return hashlib.md5(base_string.encode()).hexdigest()

# 获取公司研报 signature
def get_research_signature(params):
    secret = "sjdxfnqogbzoun13d971ckh8p"
    timestamp = str(int(time.time() * 1000))
    sorted_keys = sorted(params.keys())
    values_str = "".join([params[k] for k in sorted_keys])
    sign_str = secret + values_str + timestamp
    sign = hashlib.md5(sign_str.encode()).hexdigest()
    return sign, timestamp


# ========== 实时数据采集函数 ==========

async def fetch_stock_rank(sort_type: str):
    """获取股票涨跌幅排行
    sort_type: '0' 涨幅榜, '1' 跌幅榜
    """
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
                # 获取价格，优先使用 closePx，如果没有则使用 lastPx
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


async def fetch_sector_rank(hq_type_code: str):
    """获取行业/概念板块排行"""
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


async def fetch_research_reports(page: int = 0, page_size: int = 50):
    """获取研报数据"""
    reports = []
    today = datetime.now()
    from_date = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    to_date = today.strftime("%Y-%m-%d")
    
    params = {
        'pageNo': str(page),
        'pageSize': str(page_size),
        'from': from_date,
        'to': to_date,
    }
    
    try:
        sign, timestamp = get_research_signature(params)
        
        headers = {
            'referer': 'https://stock.9fzt.com/',
            'signature': sign,
            'timestamp': timestamp,
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }
        
        url = 'https://api-hq.chongnengjihua.com/news/api/1/company/report/list'
        response = requests.get(url, headers=headers, params=params, timeout=15)
        data = response.json()
        
        if data.get('code') != 1:
            logger.warning(f"研报接口返回错误: {data}")
            return []
        
        infos = data.get('data', {}).get('data', [])
        for item in infos:
            rating_value = item.get('orgDescription', 0)
            rating = f"{rating_value}星" if rating_value and rating_value > 0 else "关注"
            
            reports.append({
                "id": item.get('reportId', ''),
                "stock_code": item.get('symbol', ''),
                "stock_name": item.get('stockName', ''),
                "title": item.get('title', '研究报告'),
                "publisher": item.get('orgNameDisc', '九方智投'),
                "rating": rating,
                "publish_date": datetime.fromtimestamp(item.get('publishDate', 0)).strftime("%Y-%m-%d") if item.get('publishDate') else "",
                "summary": item.get('title', '点击查看详细内容')
            })
        
        logger.info(f"获取到 {len(reports)} 条研报")
        return reports
    except Exception as e:
        logger.error(f"获取研报错误: {e}")
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


# ========== 实时行情接口（从九方智投获取）==========

@app.get("/api/realtime/ranks")
async def get_realtime_ranks(rank_type: str = Query("up", description="up/down")):
    """获取实时涨跌幅榜单（从九方智投）"""
    sort_type = '0' if rank_type == 'up' else '1'
    try:
        ranks = await fetch_stock_rank(sort_type)
        return {
            "code": 200,
            "message": "success",
            "data": ranks,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "九方智投"
        }
    except Exception as e:
        logger.error(f"获取实时榜单错误: {e}")
        return {"code": 500, "message": str(e), "data": []}


@app.get("/api/realtime/industry")
async def get_realtime_industry():
    """获取实时行业板块排行（从九方智投）"""
    try:
        industries = await fetch_sector_rank('HY')
        # 格式化返回
        formatted = []
        for item in industries:
            formatted.append({
                "industry_code": item.get("sector_code", ""),
                "industry_name": item.get("sector_name", ""),
                "change_percent": item.get("change_percent", 0)
            })
        return {
            "code": 200,
            "message": "success",
            "data": formatted,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "九方智投"
        }
    except Exception as e:
        logger.error(f"获取行业板块错误: {e}")
        return {"code": 500, "message": str(e), "data": []}


@app.get("/api/realtime/concept")
async def get_realtime_concept():
    """获取实时概念板块排行（从九方智投）"""
    try:
        concepts = await fetch_sector_rank('GN')
        formatted = []
        for item in concepts:
            formatted.append({
                "concept_code": item.get("sector_code", ""),
                "concept_name": item.get("sector_name", ""),
                "change_percent": item.get("change_percent", 0)
            })
        return {
            "code": 200,
            "message": "success",
            "data": formatted,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "九方智投"
        }
    except Exception as e:
        logger.error(f"获取概念板块错误: {e}")
        return {"code": 500, "message": str(e), "data": []}


@app.get("/api/market/limit-stats")
async def get_limit_stats():
    """获取涨跌停统计（模拟数据）"""
    return {
        "limit_up_count": 45,
        "limit_down_count": 12,
        "sentiment": 65
    }


# ========== 个股搜索接口 ==========
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
            SELECT w.stock_code, s.stock_name, w.alert_threshold
            FROM watchlists w
            JOIN stocks s ON w.stock_code = s.stock_code
            WHERE w.user_id = $1
            ORDER BY w.added_at
        """, user_id)
        
        return [{
            "stock_code": row['stock_code'],
            "stock_name": row['stock_name'],
            "price": 0,
            "change_percent": 0,
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
            
            # 如果股票不存在，先插入
            stock_exists = await conn.fetchval("SELECT 1 FROM stocks WHERE stock_code = $1", stock_code)
            if not stock_exists:
                await conn.execute("""
                    INSERT INTO stocks (stock_code, stock_name, market)
                    VALUES ($1, $2, $3)
                """, stock_code, stock_code, 'Unknown')
            
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


# ========== 个股详情接口 ==========
@app.get("/api/stock/detail")
async def get_stock_detail(stock_code: str):
    """获取个股详情"""
    # 从实时数据中查找
    try:
        ranks = await fetch_stock_rank('0')
        for item in ranks:
            if item['stock_code'] == stock_code:
                return {
                    "stock_code": item['stock_code'],
                    "stock_name": item['stock_name'],
                    "quote": {
                        "last_price": item['price'],
                        "change_percent": item['change_percent'],
                        "volume": item.get('volume', 0),
                        "amount": item.get('amount', 0)
                    }
                }
        
        # 如果没找到，返回空数据
        return {
            "stock_code": stock_code,
            "stock_name": stock_code,
            "quote": {
                "last_price": 0,
                "change_percent": 0,
                "volume": 0,
                "amount": 0
            }
        }
    except Exception as e:
        logger.error(f"获取个股详情错误: {e}")
        return {
            "stock_code": stock_code,
            "stock_name": stock_code,
            "quote": {
                "last_price": 0,
                "change_percent": 0,
                "volume": 0,
                "amount": 0
            }
        }


# ========== 研报接口 ==========
@app.get("/api/research/jiufang")
async def get_jiufang_research(
    page: int = Query(0, ge=0, le=10),
    page_size: int = Query(50, ge=1, le=100)
):
    """获取九方智投最新研报"""
    try:
        reports = await fetch_research_reports(page, page_size)
        
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
            return {
                "code": 200,
                "message": "success",
                "data": [],
                "total": 0,
                "source": "暂无数据"
            }
    except Exception as e:
        logger.error(f"获取九方智投研报异常: {e}")
        return {"code": 500, "message": str(e), "data": []}


@app.get("/api/research/stock")
async def get_stock_research(stock_code: str, limit: int = Query(20, ge=1, le=50)):
    """获取个股研报"""
    try:
        # 从九方智投获取个股研报（筛选）
        reports = await fetch_research_reports(0, 100)
        filtered = [r for r in reports if r['stock_code'] == stock_code][:limit]
        return filtered
    except Exception as e:
        logger.error(f"获取个股研报错误: {e}")
        return []


# ========== 定时任务 ==========
scheduler = BackgroundScheduler()

def scheduled_data_fetch():
    """定时采集数据（可选）"""
    pass

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
