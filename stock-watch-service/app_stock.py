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
# ========== 九方智投搜索接口 ==========
@app.get("/api/stock/search")
async def search_stock(keyword: str):
    """搜索股票（从九方智投实时获取）"""
    try:
        # 从涨幅榜获取数据（包含了股票列表）
        up_ranks = await fetch_stock_rank('0')
        down_ranks = await fetch_stock_rank('1')
        
        # 合并去重
        all_stocks = {}
        for item in up_ranks + down_ranks:
            if item['stock_code'] not in all_stocks:
                all_stocks[item['stock_code']] = item
        
        stocks = list(all_stocks.values())
        
        # 按关键词搜索
        results = []
        keyword_lower = keyword.lower()
        for stock in stocks:
            if (keyword_lower in stock['stock_code'].lower() or 
                keyword_lower in stock['stock_name'].lower()):
                results.append({
                    "stock_code": stock['stock_code'],
                    "stock_name": stock['stock_name'],
                    "market": "Unknown",
                    "industry": ""
                })
        
        return results[:20]
    except Exception as e:
        logger.error(f"搜索股票错误: {e}")
        return []


# ========== 自选股接口（使用内存存储）==========
# 由于不使用数据库，用内存字典存储自选股
watchlist_storage = {}

@app.get("/api/watchlist")
async def get_watchlist(user_id: str):
    """获取自选股列表"""
    user_watchlist = watchlist_storage.get(user_id, [])
    
    # 从实时数据获取最新价格
    try:
        up_ranks = await fetch_stock_rank('0')
        down_ranks = await fetch_stock_rank('1')
        
        all_stocks = {}
        for item in up_ranks + down_ranks:
            all_stocks[item['stock_code']] = item
        
        result = []
        for stock_code in user_watchlist:
            stock_info = all_stocks.get(stock_code, {})
            result.append({
                "stock_code": stock_code,
                "stock_name": stock_info.get('stock_name', stock_code),
                "price": stock_info.get('price', 0),
                "change_percent": stock_info.get('change_percent', 0),
                "alert_threshold": 3.0
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
        
        if user_id not in watchlist_storage:
            watchlist_storage[user_id] = []
        
        if stock_code not in watchlist_storage[user_id]:
            watchlist_storage[user_id].append(stock_code)
        
        return {"success": True, "message": "已添加到自选股"}
    except Exception as e:
        logger.error(f"添加自选股错误: {e}")
        return {"success": False, "message": str(e)}


@app.delete("/api/watchlist")
async def remove_watchlist(user_id: str, stock_code: str):
    """删除自选股"""
    if user_id in watchlist_storage:
        if stock_code in watchlist_storage[user_id]:
            watchlist_storage[user_id].remove(stock_code)
    return {"success": True, "message": "已从自选股删除"}


@app.post("/api/watchlist/alert")
async def set_alert(request: Request):
    """设置涨跌提醒"""
    try:
        body = await request.json()
        # 暂存提醒设置（可扩展）
        return {"success": True, "message": "提醒设置成功"}
    except Exception as e:
        logger.error(f"设置提醒错误: {e}")
        return {"success": False, "message": str(e)}


# ========== 个股详情接口（从九方智投实时获取）==========
@app.get("/api/stock/detail")
async def get_stock_detail(stock_code: str):
    """获取个股详情（从九方智投实时获取）"""
    try:
        # 从涨幅榜和跌幅榜中查找
        up_ranks = await fetch_stock_rank('0')
        down_ranks = await fetch_stock_rank('1')
        
        all_stocks = {}
        for item in up_ranks + down_ranks:
            all_stocks[item['stock_code']] = item
        
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



# ========== 微信提醒功能 ==========
import asyncio
import threading
from datetime import datetime, timedelta

# 提醒记录存储（避免重复推送）
alert_record = {}

# 微信小程序订阅消息模板ID（需要在微信公众平台申请）
# 模板示例：涨跌提醒模板
TEMPLATE_ID = "你的模板ID"  # 需要在微信公众平台申请订阅消息模板

async def check_and_send_alerts():
    """检查自选股涨跌幅并发送提醒"""
    try:
        # 获取实时行情
        up_ranks = await fetch_stock_rank('0')
        down_ranks = await fetch_stock_rank('1')
        
        # 建立行情映射
        quote_map = {}
        for item in up_ranks + down_ranks:
            quote_map[item['stock_code']] = item
        
        # 遍历所有用户的自选股
        for user_id, watchlist in watchlist_storage.items():
            for stock_code in watchlist:
                # 获取提醒阈值（默认3%）
                threshold = user_alert_settings.get(user_id, {}).get(stock_code, 3.0)
                
                # 获取实时行情
                quote = quote_map.get(stock_code)
                if not quote:
                    continue
                
                change_percent = quote.get('change_percent', 0)
                stock_name = quote.get('stock_name', stock_code)
                price = quote.get('price', 0)
                
                # 检查是否触发提醒
                alert_key = f"{user_id}_{stock_code}_{datetime.now().strftime('%Y-%m-%d')}"
                
                # 涨幅超过阈值且今日未提醒过
                if change_percent >= threshold and alert_key not in alert_record:
                    alert_record[alert_key] = True
                    # 发送微信提醒
                    await send_wechat_alert(user_id, stock_code, stock_name, price, change_percent, "上涨")
                # 跌幅超过阈值（可选，-3%）
                elif change_percent <= -threshold and alert_key not in alert_record:
                    alert_record[alert_key] = True
                    await send_wechat_alert(user_id, stock_code, stock_name, price, change_percent, "下跌")
                    
        # 清理7天前的提醒记录
        clean_old_records()
        
    except Exception as e:
        logger.error(f"检查提醒错误: {e}")


async def send_wechat_alert(user_id, stock_code, stock_name, price, change_percent, alert_type):
    """发送微信订阅消息"""
    try:
        # 获取用户的openid（需要从用户登录信息获取）
        openid = user_openid_map.get(user_id)
        if not openid:
            logger.warning(f"用户 {user_id} 未绑定openid")
            return
        
        # 构建消息内容
        data = {
            "thing1": {"value": stock_name},  # 股票名称
            "amount2": {"value": f"{price:.2f}"},  # 当前价格
            "thing3": {"value": f"{alert_type}{abs(change_percent):.2f}%"},  # 涨跌幅
            "time4": {"value": datetime.now().strftime("%H:%M")},  # 时间
            "thing5": {"value": f"您的自选股{stock_name}{alert_type}{abs(change_percent):.2f}%，请注意查看"}  # 提醒内容
        }
        
        # 调用微信小程序服务端API
        access_token = await get_access_token()
        url = f"https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token={access_token}"
        
        payload = {
            "touser": openid,
            "template_id": TEMPLATE_ID,
            "page": f"pages/stock-detail/stock-detail?code={stock_code}",
            "data": data
        }
        
        response = requests.post(url, json=payload, timeout=10)
        result = response.json()
        
        if result.get('errcode') == 0:
            logger.info(f"发送提醒成功: {stock_name} {alert_type}{change_percent}%")
        else:
            logger.error(f"发送提醒失败: {result}")
            
    except Exception as e:
        logger.error(f"发送微信提醒错误: {e}")


async def get_access_token():
    """获取微信小程序access_token"""
    # 从环境变量获取配置
    appid = os.environ.get("WECHAT_APPID", "")
    secret = os.environ.get("WECHAT_SECRET", "")
    
    # 缓存token
    if hasattr(get_access_token, "token") and hasattr(get_access_token, "expire_time"):
        if datetime.now() < get_access_token.expire_time:
            return get_access_token.token
    
    url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={appid}&secret={secret}"
    response = requests.get(url, timeout=10)
    data = response.json()
    
    if data.get('access_token'):
        get_access_token.token = data['access_token']
        get_access_token.expire_time = datetime.now() + timedelta(seconds=7000)
        return get_access_token.token
    else:
        logger.error(f"获取access_token失败: {data}")
        return None


def clean_old_records():
    """清理7天前的提醒记录"""
    now = datetime.now()
    keys_to_delete = []
    for key in alert_record:
        try:
            # 从key中提取日期
            date_str = key.split('_')[-1]
            record_date = datetime.strptime(date_str, '%Y-%m-%d')
            if (now - record_date).days >= 7:
                keys_to_delete.append(key)
        except:
            pass
    
    for key in keys_to_delete:
        del alert_record[key]


# 用户openid映射（需要在小程序登录时保存）
user_openid_map = {}

# 用户提醒阈值设置
user_alert_settings = {}


# ========== 用户登录接口 ==========
@app.post("/api/user/login")
async def user_login(request: Request):
    """小程序登录，保存openid"""
    try:
        body = await request.json()
        code = body.get('code')  # 微信登录code
        user_id = body.get('user_id')  # 前端生成的用户标识
        
        # 用code换取openid
        appid = os.environ.get("WECHAT_APPID", "")
        secret = os.environ.get("WECHAT_SECRET", "")
        url = f"https://api.weixin.qq.com/sns/jscode2session?appid={appid}&secret={secret}&js_code={code}&grant_type=authorization_code"
        
        response = requests.get(url, timeout=10)
        data = response.json()
        
        openid = data.get('openid')
        if openid:
            user_openid_map[user_id] = openid
            return {"success": True, "openid": openid}
        else:
            return {"success": False, "message": "登录失败"}
    except Exception as e:
        logger.error(f"登录错误: {e}")
        return {"success": False, "message": str(e)}


# ========== 提醒设置接口 ==========
@app.post("/api/watchlist/alert")
async def set_alert(request: Request):
    """设置涨跌提醒阈值"""
    try:
        body = await request.json()
        user_id = body.get('user_id')
        stock_code = body.get('stock_code')
        threshold = body.get('threshold', 3.0)
        enabled = body.get('enabled', True)
        
        if user_id not in user_alert_settings:
            user_alert_settings[user_id] = {}
        
        if enabled:
            user_alert_settings[user_id][stock_code] = threshold
        else:
            if stock_code in user_alert_settings[user_id]:
                del user_alert_settings[user_id][stock_code]
        
        return {"success": True, "message": f"提醒设置成功，涨跌超过{threshold}%将收到微信通知"}
    except Exception as e:
        logger.error(f"设置提醒错误: {e}")
        return {"success": False, "message": str(e)}


@app.get("/api/watchlist/alert/settings")
async def get_alert_settings(user_id: str):
    """获取用户的提醒设置"""
    settings = user_alert_settings.get(user_id, {})
    return [
        {"stock_code": code, "threshold": threshold}
        for code, threshold in settings.items()
    ]


# ========== 启动定时任务 ==========
def start_alert_checker():
    """启动定时检查提醒的线程"""
    def check_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while True:
            try:
                # 只在交易时段检查（9:30-15:00）
                now = datetime.now()
                if 9 <= now.hour <= 15:
                    loop.run_until_complete(check_and_send_alerts())
                time.sleep(60)  # 每分钟检查一次
            except Exception as e:
                logger.error(f"提醒检查循环错误: {e}")
                time.sleep(60)
    
    thread = threading.Thread(target=check_loop, daemon=True)
    thread.start()

# 启动提醒检查线程
start_alert_checker()











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
