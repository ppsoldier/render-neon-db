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
    return create_engine(DATABASE_URL)


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
        
        # 准备数据
        df_copy = df.copy()
        df_copy['date'] = today
        
        # 选择需要的列
        cols = ['code', 'name', 'price', 'change_pct', 'total_score', 'advice', 'fin_rating', 'date']
        existing_cols = [c for c in cols if c in df_copy.columns]
        df_copy = df_copy[existing_cols]
        
        # 保存
        df_copy.to_sql(
            'selected_stocks',
            engine,
            schema=SCHEMA_NAME,
            if_exists='append',
            index=False
        )
        logger.info(f"选股结果已保存到数据库: {len(df_copy)} 条")
        
    except Exception as e:
        logger.error(f"保存选股结果失败: {e}")
