# 股票分析系统 - 配置文件
# 请根据实际情况修改以下配置
import os

# 获取当前文件所在目录（stock_analysis_system）
_CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
# 动态路径
OUTPUT_DIR = os.path.join(_CONFIG_DIR, "output")
DATA_CACHE_DIR = os.path.join(_CONFIG_DIR, "data_cache")
# 如果需要获取项目根目录（stock_analysis_system 的父目录）
PROJECT_ROOT = os.path.dirname(_CONFIG_DIR)   # 此时 _CONFIG_DIR 已经定义，不会有红线

# ========== 数据采集配置 ==========
DATA_SOURCES = {
    "jiufang": {
        "enabled": True,
        "request_interval": 1,    # 请求间隔（秒）
    },
    "eastmoney": {
        "enabled": False,
        "base_url": "https://push2.eastmoney.com/api/qt/clist/get",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://quote.eastmoney.com/",
        },
        "request_interval": 2,
    },
    "tonghuashun": {
        "enabled": False,
        "base_url": "https://dq.10jqka.com.cn/fuyao/hot_list_data/out",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.10jqka.com.cn/",
        },
        "request_interval": 2,
    },
}

# ========== 第一层因子筛选配置 ==========
FACTOR_FILTER = {
    "momentum_days": 20,
    "min_momentum": 0.05,       # 20日涨幅 > 5%
    "volatility_days": 60,
    "max_volatility": 0.40,     # 60日波动率 < 40%
    "min_market_cap": 50,       # 可选，与 STOCK_FILTER 保持一致
    "max_market_cap": 2000,
}


# ========== 选股策略参数 ==========
STOCK_FILTER = {
    "min_market_cap": 50,        # 最小市值（亿元）
    "max_market_cap": 2000,      # 最大市值（亿元）
    "min_volume_ratio": 1.5,     # 最小量比（从0.8提高到1.5）
    "max_pe_ratio": 200,         # 最大市盈率
    "min_pe_ratio": 0,           # 最小市盈率（排除亏损）
    "min_turnover_rate": 1.0,    # 最小换手率（%）
    "max_turnover_rate": 25.0,   # 最大换手率（%）
    "min_rise_pct": -5.0,        # 最小涨幅（%）
    "max_rise_pct": 9.5,         # 最大涨幅（%）
}

# ========== 技术指标参数 ==========
TECHNICAL_PARAMS = {
    "ma_periods": [5, 10, 20, 60],  # 均线周期
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "rsi_period": 14,
    "boll_period": 20,
    "boll_std": 2,
    "kdj_period": 9,
    "max_amplitude": 15,         # 最大振幅（%）
}

# ========== 评分权重 ==========
SCORE_WEIGHTS = {
    "trend_strength": 0.12,      # 趋势强度
    "volume_activity": 0.12,     # 量能活跃度
    "capital_flow": 0.25,        # 资金流向
    "technical_signal": 0.16,    # 技术信号
    "market_sentiment": 0.08,    # 市场情绪
    "risk_control": 0.08,        # 风险控制
    "position": 0.09,            # 股价位置
    "news_event": 0.10,          # 消息面事件
}

# ========== 交易信号阈值 ==========
SIGNAL_THRESHOLDS = {
    "strong_buy": 80,           # 强烈买入
    "buy": 65,                  # 买入
    "hold": 45,                 # 持有/观望
    "sell": 35,                 # 卖出
    "strong_sell": 20,          # 强烈卖出
}

# ========== 券商API配置（需用户自行填写）==========
BROKER_CONFIG = {
    "broker_name": "",          # 券商名称：htsc(华泰)/gf(广发)/zszq(招商)/citic(中信)等
    "account": "",              # 资金账号
    "password": "",             # 交易密码
    "api_key": "",              # API密钥（如有）
    "api_secret": "",           # API密钥密文（如有）
    "trade_server": "",         # 交易服务器地址
    "enable_auto_trade": False, # 是否启用自动交易（强烈建议先用模拟盘测试）
}

# ========== 风险控制 ==========
RISK_CONTROL = {
    "max_position_per_stock": 0.2,   # 单只股票最大仓位
    "max_total_position": 0.8,       # 总仓位上限
    "stop_loss_pct": -7.0,           # 止损线（%）
    "take_profit_pct": 15.0,         # 止盈线（%）
    "max_daily_trades": 5,           # 每日最大交易次数
    "max_daily_loss": -3.0,          # 每日最大亏损比例（%）
}

# ========== LLM 分析引擎配置 ==========
LLM_CONFIG = {
    "enable_llm": True,            # 是否启用 LLM 分析（需要配置 api_key）
    "enable_fallback": True,        # LLM 不可用时自动降级为规则评分
    "api_key": "sk-xvtfcqzorcnsrymgpstmxclhjqjbvqawmiegiuezoqjwzcjr",
    "base_url": "https://api.siliconflow.cn/v1",
    "model": "deepseek-ai/DeepSeek-V3",
    "temperature": 0.3,
    "max_tokens": 2048,
    "max_llm_stocks": 30,
}

# ========== 性能优化配置 ==========
PERFORMANCE = {
    "stock_pages": 277,         # 个股采集页数（每页20只，100页=2000只）  最大277页
    "concept_pages": 14,         # 概念板块页数（5页=150个）  最大14页
    "industry_pages": 5,        # 行业板块页数（3页=90个）   最大5页
    "max_technical_stocks": 100, # 最多计算多少只股票的技术指标
    "parallel_workers": 5,      # 并行线程数
}

# ========== 消息面事件驱动配置 ==========
NEWS_CONFIG = {
    "enabled": True,                    # 是否启用消息面选股
    "minutes": 120,                     # 只关注过去120分钟内的新闻
    "max_boost": 20,                    # 消息面最高加分

    # 事件类型及权重（利好为正，利空为负）
    "event_weights": {
        # 业绩相关
        "净利润": 8, "营收增长": 10, "预增": 15, "扭亏": 12,
        "亏损": -8, "下滑": -5, "承压": -3,

        # 资金流向
        "资金净流入": 8, "主力资金": 5, "融资": 4,

        # 重大事件
        "中标": 15, "重大合同": 15, "战略合作": 12,
        "回购": 10, "增持": 10, "股权激励": 8,
        "新产品": 12, "技术突破": 15, "获批": 12,
        "并购重组": 18, "举牌": 12, "高送转": 8,

        # 利空事件
        "减持": -10, "大股东减持": -12,
        "警示": -15, "问询": -10, "监管": -15,
        "下调": -10, "解禁": -8, "延期": -5,
    },

    # 新闻来源权重
    "source_weights": {
        "公司公告": 1.2,
        "交易所": 1.2,
        "媒体报道": 0.8,
    },

    # 热度衰减参数（分钟）
    "decay_minutes": 60,
}

# ========== 持仓股持续跟踪配置 ==========
POSITION_TRACKING = {
    "enabled": True,                # 是否启用跟踪
    "interval": 60,                 # 轮询间隔（秒），建议 60-300
    "enable_push": True,            # 是否自动推送企业微信
    "thresholds": {
        "stop_loss_pct": -8.0,      # 止损线（%）
        "take_profit_pct": 15.0,    # 止盈线（%）
        "add_position_pct": -5.0,   # 加仓线（%）
        "reduce_position_pct": 10.0, # 减仓线（%）
        "high_swing_rsi": 75,       # 高抛 RSI 阈值
        "low_absorb_rsi": 30,       # 低吸 RSI 阈值
        "golden_cross_buy": True,   # 金叉是否作为加仓信号
        "ma_trend_period": 20,      # 趋势判断均线周期
    }
}

# ========== 企业微信推送配置 ==========
WECHAT_WEBHOOK_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=18cd9357-0b8b-4114-a87e-ece5cc01fd9f"
WECHAT_ENABLED = True

# ========== 自选股配置 ==========
WATCHLIST_FILE = "watchlist.json"

# ========== 数据库配置 ==========
DB_ENABLED = True  # 是否启用数据库保存

# ========== 日志与输出 ==========
LOG_LEVEL = "INFO"
