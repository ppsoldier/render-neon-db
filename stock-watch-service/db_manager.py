# db_manager.py
import os
import pandas as pd
from datetime import datetime
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from loguru import logger

# 数据库配置（从环境变量读取）
DB_HOST = os.environ.get("DB_HOST", "ep-rapid-frog-ani7chkm.c-6.us-east-1.aws.neon.tech")
DB_USER = os.environ.get("DB_USER", "neondb_owner")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "npg_b1QR9lMdusev")
DB_NAME = os.environ.get("DB_NAME", "neondb")
DB_PORT = int(os.environ.get("DB_PORT", 5432))
SCHEMA_NAME = os.environ.get("DB_SCHEMA", "stock_data")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?sslmode=require"


def get_engine():
    """获取同步数据库引擎"""
    return create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=5,
        max_overflow=10
    )


def init_database():
    """初始化数据库：创建 schema 和所有表"""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            # 创建 schema（如果不存在）
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA_NAME}"))
            conn.commit()
            
            # 创建 selected_stocks 表
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.selected_stocks (
                    id SERIAL PRIMARY KEY,
                    date DATE NOT NULL,
                    code VARCHAR(10) NOT NULL,
                    name VARCHAR(50),
                    price DECIMAL(10,2),
                    change_pct DECIMAL(10,2),
                    total_score DECIMAL(10,2),
                    advice VARCHAR(20),
                    fin_rating VARCHAR(50),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            
            # 创建 market_data 表
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.market_data (
                    id SERIAL PRIMARY KEY,
                    date DATE UNIQUE NOT NULL,
                    market_state VARCHAR(20),
                    market_score INTEGER,
                    position_ratio DECIMAL(5,2),
                    advice VARCHAR(100),
                    trend_strength DECIMAL(10,2),
                    volatility DECIMAL(10,2),
                    ma_deviation DECIMAL(10,2),
                    ma_arrangement VARCHAR(20),
                    index_position DECIMAL(10,2),
                    recent_return DECIMAL(10,2),
                    vol_ratio DECIMAL(10,2),
                    limit_up_count INTEGER DEFAULT 0,
                    limit_down_count INTEGER DEFAULT 0,
                    up_count INTEGER DEFAULT 0,
                    down_count INTEGER DEFAULT 0,
                    advance_percent DECIMAL(5,2),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            
            # 创建 stocks_data 表
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.stocks_data (
                    id SERIAL PRIMARY KEY,
                    date DATE NOT NULL,
                    code VARCHAR(10) NOT NULL,
                    name VARCHAR(50),
                    price DECIMAL(10,2),
                    change_pct DECIMAL(10,2),
                    volume BIGINT,
                    amount DECIMAL(15,2),
                    turnover_rate DECIMAL(10,2),
                    pe_ratio DECIMAL(10,2),
                    market_cap DECIMAL(15,2),
                    volume_ratio DECIMAL(10,2),
                    amplitude DECIMAL(10,2),
                    main_inflow DECIMAL(15,2),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            
            # 创建 concepts_data 表
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.concepts_data (
                    id SERIAL PRIMARY KEY,
                    date DATE NOT NULL,
                    concept_name VARCHAR(100),
                    concept_code VARCHAR(20),
                    change_pct DECIMAL(10,2),
                    leading_stock VARCHAR(50),
                    stock_count INTEGER,
                    up_count INTEGER,
                    Fundflow DECIMAL(15,2),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            
            # 创建 industries_data 表
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.industries_data (
                    id SERIAL PRIMARY KEY,
                    date DATE NOT NULL,
                    industry_name VARCHAR(100),
                    change_pct DECIMAL(10,2),
                    leading_stock VARCHAR(50),
                    stock_count INTEGER,
                    up_count INTEGER,
                    Fundflow DECIMAL(15,2),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            
            # 创建 watchlist 表
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.watchlist (
                    id SERIAL PRIMARY KEY,
                    code VARCHAR(10) NOT NULL UNIQUE,
                    name VARCHAR(50),
                    added_date DATE DEFAULT CURRENT_DATE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            
            # 创建 current_positions 表
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.current_positions (
                    id SERIAL PRIMARY KEY,
                    code VARCHAR(10) NOT NULL UNIQUE,
                    name VARCHAR(50),
                    quantity DECIMAL(15,2),
                    cost_price DECIMAL(10,2),
                    market_price DECIMAL(10,2),
                    market_value DECIMAL(15,2),
                    pnl DECIMAL(15,2),
                    pnl_pct DECIMAL(10,2),
                    updated_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            
            # 创建 operation_log 表
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.operation_log (
                    id SERIAL PRIMARY KEY,
                    log_date DATE NOT NULL,
                    log_level VARCHAR(20),
                    module VARCHAR(50),
                    content TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            
            # 创建索引
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_selected_date ON {SCHEMA_NAME}.selected_stocks(date)"))
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_stocks_date ON {SCHEMA_NAME}.stocks_data(date)"))
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_market_date ON {SCHEMA_NAME}.market_data(date)"))
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_concepts_date ON {SCHEMA_NAME}.concepts_data(date)"))
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_industries_date ON {SCHEMA_NAME}.industries_data(date)"))
            
            conn.commit()
            logger.info(f"数据库表初始化完成 (Schema: {SCHEMA_NAME})")
            
    except Exception as e:
        logger.error(f"初始化数据库表失败: {e}")
        raise


def get_latest_picks(limit: int = 10):
    """获取最新的选股结果"""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            result = conn.execute(
                text(f"""
                    SELECT code, name, price, change_pct, total_score, advice, fin_rating, date
                    FROM {SCHEMA_NAME}.selected_stocks
                    WHERE date = (
                        SELECT MAX(date) FROM {SCHEMA_NAME}.selected_stocks
                    )
                    ORDER BY total_score DESC
                    LIMIT :limit
                """),
                {"limit": limit}
            )
            rows = result.fetchall()
        
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
        return picks
    except Exception as e:
        logger.error(f"获取最新选股结果失败: {e}")
        return []


def save_selected_stocks(df: pd.DataFrame, delete_existing: bool = True):
    """保存选股结果到数据库"""
    if df.empty:
        logger.warning("DataFrame 为空，跳过保存")
        return
    
    try:
        engine = get_engine()
        today = datetime.now().strftime("%Y-%m-%d")
        
        with engine.connect() as conn:
            if delete_existing:
                conn.execute(
                    text(f"DELETE FROM {SCHEMA_NAME}.selected_stocks WHERE date = :date"),
                    {"date": today}
                )
                conn.commit()
                logger.info(f"已删除 {today} 的旧选股数据")
        
        # 准备数据
        df_copy = df.copy()
        df_copy['date'] = today
        
        # 选择需要的列
        cols = ['code', 'name', 'price', 'change_pct', 'total_score', 'advice', 'fin_rating', 'date']
        existing_cols = [c for c in cols if c in df_copy.columns]
        df_copy = df_copy[existing_cols]
        
        # 如果缺少 advice 列，添加默认值
        if 'advice' not in df_copy.columns:
            df_copy['advice'] = '持有/观望'
        
        # 如果缺少 fin_rating 列，添加默认值
        if 'fin_rating' not in df_copy.columns:
            df_copy['fin_rating'] = '一般'
        
        # 保存
        df_copy.to_sql(
            'selected_stocks',
            engine,
            schema=SCHEMA_NAME,
            if_exists='append',
            index=False
        )
        logger.info(f"✅ 选股结果已保存到数据库: {len(df_copy)} 条")
        
    except Exception as e:
        logger.error(f"❌ 保存选股结果失败: {e}")
        raise


import numpy as np

def save_market_data(market_result: dict, delete_existing: bool = True):
    if not market_result:
        logger.warning("market_result 为空，跳过保存")
        return

    try:
        engine = get_engine()
        today = datetime.now().strftime("%Y-%m-%d")

        with engine.connect() as conn:
            if delete_existing:
                conn.execute(
                    text(f"DELETE FROM {SCHEMA_NAME}.market_data WHERE date = :date"),
                    {"date": today}
                )
                conn.commit()

            details = market_result.get('details', {})
            
            # 定义一个辅助函数来转换 NumPy 类型
            def convert_to_python(value):
                if isinstance(value, np.generic):
                    return value.item()  # 将 numpy 类型转换为 Python 原生类型
                return value

            # 构建参数时，对所有数值进行转换
            params = {
                "date": today,
                "market_state": market_result.get('market_state'),
                "market_score": market_result.get('state_score'),
                "position_ratio": market_result.get('position_ratio'),
                "advice": market_result.get('advice'),
                "trend_strength": convert_to_python(details.get('trend_strength')),
                "volatility": convert_to_python(details.get('volatility')),
                "ma_deviation": convert_to_python(details.get('ma_deviation')),
                "ma_arrangement": details.get('ma_arrangement'),
                "index_position": convert_to_python(details.get('index_position')),
                "recent_return": convert_to_python(details.get('recent_return')),
                "vol_ratio": convert_to_python(details.get('vol_ratio')),
                "limit_up_count": details.get('limit_up_count', 0),
                "limit_down_count": details.get('limit_down_count', 0),
                "up_count": details.get('up_count', 0),
                "down_count": details.get('down_count', 0),
                "advance_percent": convert_to_python(details.get('advance_percent', 0)),
            }

            conn.execute(
                text(f"""
                    INSERT INTO {SCHEMA_NAME}.market_data 
                    (date, market_state, market_score, position_ratio, advice, 
                     trend_strength, volatility, ma_deviation, ma_arrangement, 
                     index_position, recent_return, vol_ratio,
                     limit_up_count, limit_down_count, up_count, down_count, advance_percent)
                    VALUES (
                        :date, :market_state, :market_score, :position_ratio, :advice,
                        :trend_strength, :volatility, :ma_deviation, :ma_arrangement,
                        :index_position, :recent_return, :vol_ratio,
                        :limit_up_count, :limit_down_count, :up_count, :down_count, :advance_percent
                    )
                """),
                params
            )
            conn.commit()
        logger.info("✅ 大盘数据已保存")
    except Exception as e:
        logger.error(f"❌ 保存大盘数据失败: {e}")
        raise


def save_stocks_data(df: pd.DataFrame, delete_existing: bool = True):
    if df.empty:
        logger.warning("股票数据为空，跳过保存")
        return
    
    try:
        engine = get_engine()
        today = datetime.now().strftime("%Y-%m-%d")
        
        with engine.connect() as conn:
            if delete_existing:
                conn.execute(
                    text(f"DELETE FROM {SCHEMA_NAME}.stocks_data WHERE date = :date"),
                    {"date": today}
                )
                conn.commit()
        
        df_copy = df.copy()
        # 关键修复：将日期转换为字符串格式
        if 'date' in df_copy.columns:
            # 如果是 pandas Timestamp，转换为字符串
            if pd.api.types.is_datetime64_any_dtype(df_copy['date']):
                df_copy['date'] = df_copy['date'].dt.strftime('%Y-%m-%d')
            else:
                # 如果是整数或其他类型，转换为字符串
                df_copy['date'] = df_copy['date'].astype(str)
        else:
            # 如果没有 date 列，使用今天的日期
            df_copy['date'] = today
        
        # 选择需要的列
        cols = ['date', 'code', 'name', 'price', 'change_pct', 'volume', 'amount', 
                'turnover_rate', 'pe_ratio', 'market_cap', 'volume_ratio', 'amplitude', 'main_inflow']
        existing_cols = [c for c in cols if c in df_copy.columns]
        df_copy = df_copy[existing_cols]
        
        df_copy.to_sql('stocks_data', engine, schema=SCHEMA_NAME, if_exists='append', index=False)
        logger.info(f"✅ 股票数据已保存: {len(df_copy)} 条")
    except Exception as e:
        logger.error(f"❌ 保存股票数据失败: {e}")
        raise


def save_concepts_data(df: pd.DataFrame, delete_existing: bool = True):
    if df.empty:
        logger.warning("概念数据为空，跳过保存")
        return
    
    try:
        engine = get_engine()
        today = datetime.now().strftime("%Y-%m-%d")
        
        with engine.connect() as conn:
            if delete_existing:
                conn.execute(
                    text(f"DELETE FROM {SCHEMA_NAME}.concepts_data WHERE date = :date"),
                    {"date": today}
                )
                conn.commit()
        
        df_copy = df.copy()
        df_copy['date'] = today
        
        # 关键修改：将 "Fundflow" 改为 "fundflow"（全小写）
        cols = ['date', 'concept_name', 'concept_code', 'change_pct', 'leading_stock', 'stock_count', 'up_count', 'fundflow']
        existing_cols = [c for c in cols if c in df_copy.columns]
        df_copy = df_copy[existing_cols]
        
        df_copy.to_sql('concepts_data', engine, schema=SCHEMA_NAME, if_exists='append', index=False)
        logger.info(f"✅ 概念数据已保存: {len(df_copy)} 条")
    except Exception as e:
        logger.error(f"❌ 保存概念数据失败: {e}")
        raise


def save_industries_data(df: pd.DataFrame, delete_existing: bool = True):
    if df.empty:
        logger.warning("行业数据为空，跳过保存")
        return
    
    try:
        engine = get_engine()
        today = datetime.now().strftime("%Y-%m-%d")
        
        with engine.connect() as conn:
            if delete_existing:
                conn.execute(
                    text(f"DELETE FROM {SCHEMA_NAME}.industries_data WHERE date = :date"),
                    {"date": today}
                )
                conn.commit()
        
        df_copy = df.copy()
        df_copy['date'] = today
        
        # 关键修改：将 "Fundflow" 改为 "fundflow"（全小写）
        cols = ['date', 'industry_name', 'change_pct', 'leading_stock', 'stock_count', 'up_count', 'fundflow']
        existing_cols = [c for c in cols if c in df_copy.columns]
        df_copy = df_copy[existing_cols]
        
        df_copy.to_sql('industries_data', engine, schema=SCHEMA_NAME, if_exists='append', index=False)
        logger.info(f"✅ 行业数据已保存: {len(df_copy)} 条")
    except Exception as e:
        logger.error(f"❌ 保存行业数据失败: {e}")
        raise


def save_operation_log(content: str, log_level: str = "INFO", module: str = "daily_pick"):
    """保存操作日志到数据库"""
    try:
        engine = get_engine()
        today = datetime.now().strftime("%Y-%m-%d")
        
        with engine.connect() as conn:
            conn.execute(
                text(f"""
                    INSERT INTO {SCHEMA_NAME}.operation_log (log_date, log_level, module, content)
                    VALUES (:log_date, :log_level, :module, :content)
                """),
                {
                    "log_date": today,
                    "log_level": log_level,
                    "module": module,
                    "content": content[:2000]
                }
            )
            conn.commit()
        logger.debug(f"操作日志已保存: {content[:50]}...")
    except Exception as e:
        logger.error(f"保存操作日志失败: {e}")


def get_picks_by_date(date: str, limit: int = 20):
    """获取指定日期的选股结果"""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            result = conn.execute(
                text(f"""
                    SELECT code, name, price, change_pct, total_score, advice, fin_rating, date
                    FROM {SCHEMA_NAME}.selected_stocks
                    WHERE date = :date
                    ORDER BY total_score DESC
                    LIMIT :limit
                """),
                {"date": date, "limit": limit}
            )
            rows = result.fetchall()
        
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
        return picks
    except Exception as e:
        logger.error(f"获取 {date} 选股结果失败: {e}")
        return []


def get_available_dates(limit: int = 30):
    """获取有选股数据的日期列表"""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            result = conn.execute(
                text(f"""
                    SELECT DISTINCT date
                    FROM {SCHEMA_NAME}.selected_stocks
                    ORDER BY date DESC
                    LIMIT :limit
                """),
                {"limit": limit}
            )
            rows = result.fetchall()
        return [str(row[0]) for row in rows]
    except Exception as e:
        logger.error(f"获取可用日期失败: {e}")
        return []


def save_watchlist(codes: list):
    """保存自选股列表到数据库"""
    if not codes:
        return
    
    try:
        engine = get_engine()
        with engine.connect() as conn:
            # 清空现有数据
            conn.execute(text(f"DELETE FROM {SCHEMA_NAME}.watchlist"))
            
            # 插入新数据
            for code in codes:
                code = code.strip()
                if code:
                    conn.execute(
                        text(f"""
                            INSERT INTO {SCHEMA_NAME}.watchlist (code, name, added_date)
                            VALUES (:code, :name, CURRENT_DATE)
                            ON CONFLICT (code) DO UPDATE SET name = :name
                        """),
                        {"code": code, "name": code}
                    )
            conn.commit()
        logger.info(f"自选股已保存到数据库: {len(codes)} 条")
    except Exception as e:
        logger.error(f"保存自选股失败: {e}")


def get_watchlist():
    """获取自选股列表"""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            result = conn.execute(
                text(f"SELECT code, name FROM {SCHEMA_NAME}.watchlist ORDER BY id")
            )
            rows = result.fetchall()
        return [{"code": row[0], "name": row[1]} for row in rows]
    except Exception as e:
        logger.error(f"获取自选股失败: {e}")
        return []


def get_latest_selected_stocks(limit: int = 30, fallback_to_prev: bool = True):
    """
    获取最新的选股结果
    如果当天没有数据，自动获取最近一天的数据
    
    Args:
        limit: 返回数量
        fallback_to_prev: 如果当天没有数据，是否回退到前一天
    
    Returns:
        pd.DataFrame: 选股结果
    """
    try:
        engine = get_engine()
        today = datetime.now().strftime("%Y-%m-%d")
        
        with engine.connect() as conn:
            # 先查询当天是否有数据
            result = conn.execute(
                text(f"""
                    SELECT COUNT(*) 
                    FROM {SCHEMA_NAME}.selected_stocks 
                    WHERE date = :date
                """),
                {"date": today}
            )
            count = result.fetchone()[0]
            
            # 如果当天有数据，直接返回
            if count > 0:
                df = pd.read_sql(
                    text(f"""
                        SELECT code, name, price, change_pct, total_score, advice, fin_rating, date
                        FROM {SCHEMA_NAME}.selected_stocks
                        WHERE date = :date
                        ORDER BY total_score DESC
                        LIMIT :limit
                    """),
                    conn,
                    params={"date": today, "limit": limit}
                )
                logger.info(f"获取今日选股数据: {len(df)} 条")
                return df
            
            # 如果当天没有数据且允许回退
            if fallback_to_prev:
                # 获取最近有数据的日期
                result = conn.execute(
                    text(f"""
                        SELECT DISTINCT date 
                        FROM {SCHEMA_NAME}.selected_stocks 
                        ORDER BY date DESC 
                        LIMIT 1
                    """)
                )
                row = result.fetchone()
                
                if row:
                    latest_date = row[0]
                    df = pd.read_sql(
                        text(f"""
                            SELECT code, name, price, change_pct, total_score, advice, fin_rating, date
                            FROM {SCHEMA_NAME}.selected_stocks
                            WHERE date = :date
                            ORDER BY total_score DESC
                            LIMIT :limit
                        """),
                        conn,
                        params={"date": latest_date, "limit": limit}
                    )
                    logger.info(f"当天无选股数据，获取最近日期 {latest_date} 的数据: {len(df)} 条")
                    return df
                else:
                    logger.warning("数据库中无任何选股数据")
                    return pd.DataFrame()
            else:
                return pd.DataFrame()
                
    except Exception as e:
        logger.error(f"获取选股数据失败: {e}")
        return pd.DataFrame()
