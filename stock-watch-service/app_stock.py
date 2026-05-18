import os
import asyncio
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import asyncpg
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager
import logging
import json
# ========== 导入新模块 ==========
import requests
import hashlib
import time
from datetime import datetime

# ========== 拼音首字母映射表（从您提供的数据生成）==========
PINYIN_MAP = {
    'A': ['安', '艾', '爱', '阿', '奥', '澳', '埃', '哎', '哀', '皑', '蔼', '矮', '碍', '爱', '隘', '鞍', '氨', '安', '俺', '按', '暗', '岸', '胺', '案', '肮', '昂', '盎', '凹', '敖', '熬', '翱', '袄', '傲', '奥', '懊', '澳'],
    'B': ['八', '巴', '拔', '跋', '靶', '把', '坝', '霸', '罢', '爸', '白', '百', '柏', '摆', '败', '拜', '班', '般', '斑', '搬', '板', '版', '办', '半', '伴', '帮', '绑', '榜', '膀', '傍', '棒', '包', '胞', '宝', '保', '堡', '报', '抱', '暴', '爆', '北', '贝', '备', '背', '倍', '被', '奔', '本', '崩', '逼', '鼻', '比', '彼', '笔', '币', '必', '毕', '闭', '边', '编', '鞭', '扁', '便', '变', '遍', '辨', '辩', '标', '表', '别', '宾', '冰', '兵', '柄', '饼', '并', '病', '拨', '波', '玻', '伯', '泊', '勃', '博', '搏', '薄', '补', '捕', '不', '布', '步', '部'],
    'C': ['财', '采', '彩', '菜', '参', '餐', '残', '蚕', '灿', '仓', '苍', '舱', '藏', '操', '曹', '草', '策', '侧', '测', '层', '插', '查', '茶', '察', '差', '拆', '柴', '缠', '产', '铲', '长', '尝', '偿', '常', '厂', '场', '唱', '抄', '超', '朝', '潮', '炒', '车', '扯', '撤', '尘', '沉', '辰', '晨', '成', '承', '城', '乘', '程', '惩', '吃', '池', '迟', '持', '匙', '尺', '齿', '斥', '赤', '充', '冲', '虫', '重', '抽', '筹', '仇', '绸', '愁', '丑', '臭', '出', '初', '除', '厨', '雏', '础', '储', '楚', '处', '触', '川', '穿', '传', '船', '喘', '串', '窗', '床', '创', '吹', '垂', '锤', '春', '纯', '唇', '醇', '词', '此', '次', '刺', '从', '匆', '葱', '聪', '丛', '凑', '粗', '促', '催', '脆', '翠', '村', '存', '寸', '措', '错'],
    'D': ['达', '答', '打', '大', '呆', '代', '带', '待', '袋', '戴', '丹', '单', '担', '胆', '旦', '但', '弹', '蛋', '当', '挡', '党', '荡', '刀', '导', '岛', '倒', '蹈', '到', '盗', '道', '稻', '得', '德', '的', '灯', '登', '等', '邓', '凳', '低', '堤', '滴', '迪', '敌', '笛', '底', '抵', '地', '第', '帝', '弟', '递', '第', '颠', '典', '点', '电', '店', '垫', '淀', '殿', '雕', '吊', '调', '掉', '跌', '叠', '蝶', '丁', '叮', '顶', '订', '定', '东', '冬', '董', '懂', '动', '冻', '栋', '洞', '斗', '抖', '陡', '豆', '都', '督', '毒', '独', '读', '堵', '赌', '杜', '肚', '度', '渡', '端', '短', '段', '断', '锻', '堆', '队', '对', '兑', '盾', '顿', '多', '夺', '朵', '躲'],
    'E': ['俄', '额', '恶', '饿', '恩', '儿', '而', '尔', '耳', '二'],
    'F': ['发', '乏', '伐', '罚', '法', '翻', '凡', '烦', '繁', '反', '返', '犯', '饭', '泛', '方', '房', '防', '妨', '访', '放', '非', '飞', '肥', '菲', '废', '费', '分', '芬', '纷', '粉', '份', '奋', '愤', '丰', '风', '封', '峰', '锋', '蜂', '逢', '缝', '奉', '佛', '否', '夫', '肤', '孵', '扶', '服', '浮', '符', '幅', '福', '抚', '府', '辅', '腐', '父', '付', '负', '妇', '附', '复', '赴', '副', '傅', '富', '赋', '腹', '覆'],
    'G': ['嘎', '该', '改', '概', '干', '甘', '杆', '肝', '赶', '感', '敢', '刚', '钢', '缸', '纲', '高', '搞', '稿', '告', '哥', '歌', '胳', '革', '格', '葛', '隔', '个', '各', '给', '根', '跟', '更', '工', '公', '功', '攻', '供', '宫', '巩', '共', '贡', '勾', '沟', '狗', '构', '购', '够', '估', '姑', '咕', '孤', '股', '鼓', '固', '故', '顾', '瓜', '刮', '挂', '拐', '怪', '关', '观', '官', '冠', '馆', '管', '惯', '灌', '光', '广', '归', '规', '轨', '鬼', '柜', '贵', '桂', '滚', '棍', '郭', '国', '果', '过'],
    'H': ['哈', '海', '害', '含', '函', '韩', '寒', '罕', '喊', '汉', '汗', '旱', '航', '毫', '豪', '好', '号', '耗', '浩', '呵', '喝', '合', '何', '和', '河', '核', '荷', '贺', '赫', '黑', '很', '狠', '恨', '哼', '恒', '横', '衡', '轰', '烘', '红', '宏', '洪', '虹', '侯', '喉', '厚', '候', '乎', '呼', '忽', '狐', '胡', '湖', '葫', '糊', '虎', '互', '户', '护', '花', '华', '划', '画', '话', '怀', '坏', '还', '环', '缓', '换', '唤', '患', '荒', '慌', '皇', '黄', '煌', '晃', '灰', '恢', '挥', '辉', '回', '毁', '汇', '会', '绘', '惠', '慧', '婚', '浑', '混', '活', '火', '伙', '或', '货', '获', '霍'],
    'I': [],
    'J': ['击', '机', '鸡', '积', '基', '激', '及', '吉', '即', '极', '急', '疾', '集', '几', '己', '技', '季', '既', '继', '纪', '加', '家', '佳', '嘉', '甲', '假', '价', '架', '嫁', '尖', '坚', '间', '肩', '艰', '兼', '监', '减', '检', '简', '见', '建', '剑', '健', '渐', '践', '鉴', '江', '将', '讲', '奖', '降', '交', '郊', '娇', '浇', '骄', '胶', '焦', '角', '脚', '搅', '叫', '较', '教', '阶', '皆', '接', '街', '节', '劫', '杰', '洁', '结', '解', '介', '界', '借', '今', '斤', '金', '津', '仅', '紧', '尽', '进', '近', '经', '京', '惊', '晶', '睛', '精', '井', '景', '警', '净', '径', '竞', '竟', '敬', '静', '境', '镜', '九', '久', '酒', '旧', '救', '就', '居', '举', '巨', '具', '剧', '聚', '卷', '倦', '决', '觉', '绝', '军', '君', '均', '菌'],
    'K': ['卡', '开', '凯', '看', '康', '抗', '考', '靠', '科', '可', '克', '客', '肯', '空', '孔', '恐', '控', '口', '扣', '枯', '苦', '库', '酷', '夸', '快', '块', '宽', '款', '狂', '况', '矿', '亏', '捆', '困', '扩', '括', '阔'],
    'L': ['拉', '啦', '来', '兰', '拦', '蓝', '篮', '览', '懒', '烂', '郎', '朗', '浪', '劳', '老', '乐', '雷', '垒', '类', '泪', '冷', '离', '李', '里', '理', '力', '立', '丽', '利', '连', '联', '脸', '练', '恋', '凉', '亮', '量', '辽', '疗', '料', '列', '烈', '林', '临', '灵', '领', '令', '流', '刘', '龙', '隆', '路', '旅', '绿', '率', '乱', '略', '伦', '轮', '论', '罗', '落'],
    'M': ['妈', '麻', '马', '码', '骂', '吗', '买', '迈', '麦', '卖', '满', '慢', '忙', '毛', '矛', '茅', '茂', '冒', '贸', '么', '没', '眉', '梅', '媒', '煤', '美', '妹', '门', '们', '萌', '蒙', '梦', '孟', '迷', '米', '密', '蜜', '眠', '免', '面', '民', '敏', '名', '明', '鸣', '命', '摸', '模', '摩', '磨', '魔', '抹', '末', '莫', '墨', '默', '谋', '某', '母', '亩', '木', '目', '牧', '慕', '穆'],
    'N': ['拿', '那', '纳', '娜', '耐', '男', '南', '难', '脑', '闹', '呢', '内', '能', '尼', '泥', '你', '逆', '年', '念', '娘', '酿', '鸟', '牛', '扭', '纽', '农', '弄', '怒', '女', '暖', '挪', '诺'],
    'O': ['哦', '欧', '偶', '藕'],
    'P': ['拍', '排', '派', '攀', '盘', '判', '盼', '旁', '胖', '抛', '跑', '泡', '培', '赔', '陪', '佩', '配', '喷', '盆', '朋', '碰', '批', '皮', '脾', '匹', '屁', '片', '偏', '骗', '漂', '飘', '票', '拼', '贫', '频', '品', '平', '评', '凭', '苹', '屏', '瓶', '萍', '坡', '泼', '婆', '迫', '破', '扑', '铺', '普', '谱'],
    'Q': ['七', '期', '欺', '齐', '奇', '骑', '起', '气', '弃', '汽', '千', '前', '钱', '强', '墙', '亲', '青', '轻', '清', '情', '请', '求', '球', '区', '取', '去', '全', '权', '劝', '确', '群'],
    'R': ['然', '让', '扰', '热', '人', '认', '任', '日', '容', '融', '肉', '如', '入', '软', '瑞'],
    'S': ['三', '散', '桑', '扫', '色', '森', '杀', '沙', '纱', '山', '衫', '闪', '陕', '善', '商', '赏', '上', '尚', '烧', '少', '绍', '社', '设', '身', '深', '神', '升', '生', '声', '省', '圣', '失', '师', '诗', '十', '时', '食', '史', '使', '始', '士', '市', '示', '世', '事', '势', '视', '试', '是', '适', '室', '收', '手', '守', '首', '受', '售', '授', '书', '术', '数', '树', '双', '水', '顺', '说', '司', '思', '私', '斯', '四', '寺', '似', '松', '送', '诉', '速', '宿', '诉', '算', '虽', '随', '岁', '孙', '损', '所', '索'],
    'T': ['他', '它', '她', '塔', '台', '太', '态', '谈', '弹', '探', '汤', '唐', '堂', '躺', '趟', '涛', '讨', '特', '提', '题', '体', '替', '天', '田', '条', '铁', '听', '厅', '通', '同', '统', '投', '头', '透', '突', '图', '途', '土', '团', '推', '腿', '退', '脱', '驼', '妥'],
    'U': [],
    'V': [],
    'W': ['挖', '外', '弯', '完', '玩', '万', '王', '网', '往', '忘', '旺', '危', '威', '微', '为', '围', '唯', '维', '伟', '伪', '尾', '委', '卫', '未', '位', '温', '文', '闻', '稳', '问', '我', '卧', '握', '乌', '无', '吴', '五', '午', '武', '舞', '务', '物', '误', '悟'],
    'X': ['西', '吸', '希', '析', '息', '惜', '稀', '溪', '习', '席', '洗', '喜', '系', '细', '下', '夏', '先', '显', '险', '县', '现', '线', '限', '相', '香', '箱', '乡', '想', '向', '项', '象', '像', '消', '小', '校', '笑', '效', '些', '协', '斜', '写', '谢', '心', '新', '信', '兴', '星', '行', '型', '形', '幸', '性', '休', '秀', '须', '需', '虚', '许', '序', '续', '宣', '选', '学', '雪', '血', '寻', '训', '讯'],
    'Y': ['压', '呀', '烟', '延', '严', '言', '岩', '沿', '炎', '研', '颜', '衍', '掩', '眼', '演', '验', '扬', '阳', '杨', '洋', '仰', '养', '氧', '样', '要', '药', '耶', '也', '业', '叶', '页', '夜', '液', '一', '医', '依', '仪', '宜', '已', '以', '亿', '亦', '异', '役', '译', '易', '疫', '益', '谊', '逸', '意', '毅', '因', '阴', '音', '银', '引', '饮', '印', '应', '英', '迎', '盈', '影', '映', '硬', '拥', '永', '泳', '勇', '用', '优', '忧', '幽', '悠', '尤', '由', '邮', '油', '游', '友', '有', '又', '右', '幼', '于', '余', '鱼', '愉', '雨', '语', '玉', '育', '预', '遇', '愈', '元', '原', '圆', '源', '远', '院', '愿', '约', '月', '乐', '阅', '云', '运', '韵'],
    'Z': ['在', '再', '咱', '暂', '赞', '脏', '遭', '早', '造', '责', '则', '泽', '择', '怎', '增', '赠', '扎', '摘', '宅', '债', '站', '战', '张', '章', '涨', '掌', '账', '障', '招', '找', '照', '折', '者', '这', '浙', '真', '阵', '振', '镇', '争', '征', '整', '正', '证', '政', '之', '支', '知', '执', '直', '值', '职', '植', '指', '至', '志', '制', '治', '质', '智', '置', '中', '忠', '终', '钟', '种', '众', '重', '州', '周', '洲', '轴', '逐', '主', '属', '住', '助', '注', '贮', '驻', '柱', '祝', '著', '筑', '抓', '转', '庄', '装', '状', '追', '准', '资', '滋', '子', '紫', '自', '字', '宗', '总', '纵', '走', '奏', '租', '族', '组', '祖', '最', '罪', '尊', '昨', '左', '作', '坐', '座']
}
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
            SELECT w.stock_code, s.stock_name, w.alert_threshold, w.alert_enabled,
                   r.last_price, r.change_percent
            FROM watchlists w
            JOIN stocks s ON w.stock_code = s.stock_code
            LEFT JOIN realtime_quotes r ON w.stock_code = r.stock_code
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
async def add_watchlist(request: Request):
    """添加自选股"""
    try:
        body = await request.json()
        user_id = body.get('user_id')
        stock_code = body.get('stock_code')
        alert_threshold = body.get('alert_threshold', 3.0)
        
        if not user_id or not stock_code:
            return {"success": False, "message": "user_id and stock_code required"}
        
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.execute("SET search_path TO stock_watch")
            
            # 如果股票不存在，先插入股票基本信息
            stock_exists = await conn.fetchval("SELECT 1 FROM stocks WHERE stock_code = $1", stock_code)
            if not stock_exists:
                await conn.execute("""
                    INSERT INTO stocks (stock_code, stock_name, market)
                    VALUES ($1, $2, $3)
                    ON CONFLICT DO NOTHING
                """, stock_code, stock_code, 'Unknown')
            
            await conn.execute("""
                INSERT INTO watchlists (user_id, stock_code, alert_threshold, alert_enabled, added_at)
                VALUES ($1, $2, $3, $4, NOW())
                ON CONFLICT (user_id, stock_code) DO UPDATE SET
                    alert_threshold = EXCLUDED.alert_threshold,
                    alert_enabled = EXCLUDED.alert_enabled,
                    added_at = NOW()
            """, user_id, stock_code, alert_threshold, True)
            
            return {"success": True, "message": "Added to watchlist"}
    except Exception as e:
        logger.error(f"Add watchlist error: {e}")
        return {"success": False, "message": str(e)}


@app.delete("/api/watchlist")
async def remove_watchlist(user_id: str, stock_code: str):
    """删除自选股"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        await conn.execute("""
            DELETE FROM watchlists
            WHERE user_id = $1 AND stock_code = $2
        """, user_id, stock_code)
        return {"success": True, "message": "Removed from watchlist"}


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
            return {"success": True, "message": "Alert set successfully"}
    except Exception as e:
        logger.error(f"Set alert error: {e}")
        return {"success": False, "message": str(e)}

# ========== 按首字母搜索接口 ==========
@app.get("/api/stock/search-by-letter")
async def search_by_letter(letter: str):
    """按首字母搜索股票"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        
        # 获取所有股票
        rows = await conn.fetch("""
            SELECT stock_code, stock_name, market, industry
            FROM stocks
        """)
        
        letter_upper = letter.upper()
        # 获取该字母对应的汉字列表
        chinese_chars = PINYIN_MAP.get(letter_upper, [])
        
        results = []
        for row in rows:
            stock_name = row['stock_name']
            if stock_name and len(stock_name) > 0:
                first_char = stock_name[0]
                # 检查首字母是否匹配
                if first_char in chinese_chars:
                    results.append({
                        "stock_code": row['stock_code'],
                        "stock_name": row['stock_name'],
                        "market": row['market'],
                        "industry": row['industry'],
                        "first_letter": letter_upper
                    })
        
        return results

# ========== K线数据接口 ==========
@app.get("/api/stock/kline")
async def get_stock_kline(
    stock_code: str, 
    period: str = Query("daily", description="daily/weekly/monthly"),
    days: int = Query(120, ge=1, le=365)
):
    """获取K线图数据"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        
        try:
            if period == "daily":
                rows = await conn.fetch("""
                    SELECT trade_date, open, high, low, close, volume, amount, change_percent
                    FROM daily_quotes
                    WHERE stock_code = $1
                    ORDER BY trade_date DESC
                    LIMIT $2
                """, stock_code, days)
            else:
                # 周K/月K 从日K数据聚合
                rows = await conn.fetch("""
                    SELECT trade_date, open, high, low, close, volume, amount, change_percent
                    FROM daily_quotes
                    WHERE stock_code = $1
                    ORDER BY trade_date DESC
                    LIMIT $2
                """, stock_code, days)
            
            # 如果没有K线数据，返回模拟数据
            if not rows:
                import datetime
                mock_data = []
                today = datetime.date.today()
                # 模拟最近 days 天的K线数据
                for i in range(days, 0, -1):
                    date = today - datetime.timedelta(days=i)
                    # 模拟价格波动
                    base_price = 100 + (i % 50) - 25
                    mock_data.append({
                        'trade_date': date,
                        'open': base_price,
                        'high': base_price + (i % 10),
                        'low': base_price - (i % 10),
                        'close': base_price + ((i % 20) - 10),
                        'volume': 1000000 + (i * 10000),
                        'change_percent': ((i % 20) - 10) / 10
                    })
                
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
                            "volume": row['volume'],
                            "change_percent": float(row['change_percent']) if row['change_percent'] else 0
                        }
                        for row in mock_data
                    ]
                }
            
            return {
                "stock_code": stock_code,
                "period": period,
                "data": [
                    {
                        "date": row['trade_date'].strftime("%Y-%m-%d"),
                        "open": float(row['open']) if row['open'] else 0,
                        "high": float(row['high']) if row['high'] else 0,
                        "low": float(row['low']) if row['low'] else 0,
                        "close": float(row['close']) if row['close'] else 0,
                        "volume": row['volume'] if row['volume'] else 0,
                        "change_percent": float(row['change_percent']) if row['change_percent'] else 0
                    }
                    for row in reversed(rows)
                ]
            }
        except Exception as e:
            logger.error(f"K线接口错误: {e}")
            # 返回空数据而不是报错
            return {
                "stock_code": stock_code,
                "period": period,
                "data": []
            }


# ========== 研报接口 ==========
@app.get("/api/research/stock")
async def get_stock_research(stock_code: str, limit: int = Query(20, ge=1, le=50)):
    """获取个股研报"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        
        try:
            rows = await conn.fetch("""
                SELECT title, publisher, publish_date, rating, summary
                FROM research_reports
                WHERE stock_code = $1
                ORDER BY publish_date DESC
                LIMIT $2
            """, stock_code, limit)
            
            if rows:
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
            else:
                # 没有数据时返回空数组，前端显示"暂无研报"
                return []
        except Exception as e:
            logger.error(f"Stock research error: {e}")
            return []

# ========== 最新研报接口 ==========
@app.get("/api/research/latest")
async def get_latest_research(limit: int = Query(20, ge=1, le=50)):
    """获取最新研报"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        
        try:
            rows = await conn.fetch("""
                SELECT r.id, r.title, r.publisher, r.publish_date, r.rating, 
                       r.summary, r.stock_code, s.stock_name
                FROM research_reports r
                LEFT JOIN stocks s ON r.stock_code = s.stock_code
                ORDER BY r.publish_date DESC
                LIMIT $1
            """, limit)
            
            return [
                {
                    "id": row['id'],
                    "title": row['title'],
                    "stock_code": row['stock_code'],
                    "stock_name": row['stock_name'] or row['stock_code'],
                    "publisher": row['publisher'],
                    "publish_date": row['publish_date'].strftime("%Y-%m-%d") if row['publish_date'] else None,
                    "rating": row['rating'],
                    "summary": row['summary']
                }
                for row in rows
            ]
        except Exception as e:
            logger.error(f"Latest research error: {e}")
            return []

# ========== 9FZT 实时数据接口 ==========

# 获取股票涨幅榜 signature
def generate_signature(listed_sector, sort_field, sort_type, timestamp, page):
    base_string = f"sjdxfnqogbzoun13d971ckh8p{listed_sector}{page}20{sort_field}{sort_type}{timestamp}"
    return hashlib.md5(base_string.encode()).hexdigest()

# 获取股票行业板块排行榜 signature
def get_signature(time_stamp):
    base_string = f"sjdxfnqogbzoun13d971ckh8p{time_stamp}"
    return hashlib.md5(base_string.encode()).hexdigest()

# 股票涨跌幅排行（实时数据）
async def fetch_realtime_rank(sort_type: str):
    """获取实时涨跌幅排行数据
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
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        url = 'https://api-hq.chongnengjihua.com/finance/api/2/stock/a/rank/list'
        
        try:
            response = requests.get(url=url, params=params, headers=headers, timeout=10)
            data = response.json()
            
            if not data or 'data' not in data or 'infos' not in data['data']:
                break
                
            infos = data['data']['infos']
            for item in infos:
                stock_rank.append({
                    "stock_code": item['symbol'],
                    "stock_name": item['prodName'],
                    "price": item['closePx'],
                    "change_percent": round(item['pxChangeRate'] * 100, 2),
                    "volume": item.get('volume', 0),
                    "amount": item.get('turnover', 0)
                })
            time.sleep(0.5)
        except Exception as e:
            logger.error(f"Fetch realtime rank error: {e}")
            continue
    
    return stock_rank

# 获取行业/概念板块排行榜
async def fetch_sector_rank(hq_type_code: str):
    """获取板块排行榜
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
        
        sign = get_signature(timestamp)
        
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'origin': 'https://stock.9fzt.com',
            'referer': 'https://stock.9fzt.com/',
            'signature': sign,
            'timestamp': timestamp,
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        url = 'https://hq.chongnengjihua.com/rjhy-quote-sector/api/1/pc/plate/block/quote/list'
        
        try:
            response = requests.get(url=url, headers=headers, params=params)
            data = response.json()
            
            if not data or 'data' not in data or 'plate' not in data['data']:
                break
                
            plates = data['data']['plate']
            for item in plates:
                sector_rank.append({
                    "sector_code": item['ProdCode'],
                    "sector_name": item['ProdName'],
                    "price": round(item['LastPx'] / 1000, 2),
                    "change_percent": round(item['PxChangeRate'] / 100, 2)
                })
            time.sleep(0.5)
        except Exception as e:
            logger.error(f"Fetch sector rank error: {e}")
            continue
    
    return sector_rank

# ========== 新增 API 接口 ==========

@app.get("/api/realtime/ranks")
async def get_realtime_ranks(rank_type: str = Query("up", description="up/down")):
    """获取实时涨跌幅榜单（从9FZT实时获取）"""
    sort_type = '0' if rank_type == 'up' else '1'
    try:
        ranks = await fetch_realtime_rank(sort_type)
        return {
            "code": 200,
            "message": "success",
            "data": ranks,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        logger.error(f"Get realtime ranks error: {e}")
        return {
            "code": 500,
            "message": str(e),
            "data": []
        }

@app.get("/api/realtime/industry")
async def get_realtime_industry():
    """获取实时行业板块排行"""
    try:
        industries = await fetch_sector_rank('HY')
        # 格式化返回数据，确保字段名与前端一致
        formatted_data = []
        for item in industries:
            formatted_data.append({
                "industry_code": item.get("sector_code", ""),
                "industry_name": item.get("sector_name", ""),
                "change_percent": item.get("change_percent", 0)
            })
        return {
            "code": 200,
            "message": "success",
            "data": formatted_data,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        logger.error(f"Get realtime industry error: {e}")
        return {
            "code": 500,
            "message": str(e),
            "data": []
        }

@app.get("/api/realtime/concept")
async def get_realtime_concept():
    """获取实时概念板块排行"""
    try:
        concepts = await fetch_sector_rank('GN')
        # 格式化返回数据，确保字段名与前端一致
        formatted_data = []
        for item in concepts:
            formatted_data.append({
                "concept_code": item.get("sector_code", ""),
                "concept_name": item.get("sector_name", ""),
                "change_percent": item.get("change_percent", 0)
            })
        return {
            "code": 200,
            "message": "success",
            "data": formatted_data,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        logger.error(f"Get realtime concept error: {e}")
        return {
            "code": 500,
            "message": str(e),
            "data": []
        }

# 更新原有的榜单接口，可选使用实时数据
@app.get("/api/market/ranks")
async def get_market_ranks(
    rank_type: str = Query("up", description="up/down"),
    limit: int = Query(20, ge=1, le=100),
    source: str = Query("database", description="database/realtime")
):
    """获取涨跌幅榜单（可选从数据库或实时API）"""
    if source == "realtime":
        sort_type = '0' if rank_type == 'up' else '1'
        try:
            ranks = await fetch_realtime_rank(sort_type)
            ranks = ranks[:limit]
            return [
                {
                    "stock_code": item["stock_code"],
                    "stock_name": item["stock_name"],
                    "price": item["price"],
                    "change_percent": item["change_percent"],
                    "volume": item.get("volume", 0),
                    "amount": item.get("amount", 0)
                }
                for item in ranks
            ]
        except Exception as e:
            logger.error(f"Realtime rank error: {e}")
            # 失败时回退到数据库
    
    # 从数据库获取（原有逻辑）
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

# ========== 九方智投研报接口（修正版）==========
import requests
import hashlib
import time
from datetime import datetime

def get_real_signature(params):
    """生成九方智投接口签名"""
    secret = "sjdxfnqogbzoun13d971ckh8p"
    timestamp = str(int(time.time() * 1000))

    # 核心：按键排序 → 只拼接参数值（完全匹配JS）
    sorted_keys = sorted(params.keys())
    values_str = "".join([params[k] for k in sorted_keys])

    # 签名拼接：密钥 + 值字符串 + 时间戳
    sign_str = secret + values_str + timestamp
    sign = hashlib.md5(sign_str.encode()).hexdigest()

    return sign, timestamp


async def fetch_jiufang_research(page: int = 1, page_size: int = 20):
    """从九方智投获取研报数据"""
    try:
        # 构建请求参数
        params = {
            'pageNum': str(page),
            'pageSize': str(page_size),
            'listedSector': '0',
            'sortField': 'publishTime',
            'sortType': '0',  # 0: 降序（最新），1: 升序
        }
        
        # 生成签名和时间戳
        signature, timestamp = get_real_signature(params)
        
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'origin': 'https://www.9fzt.com',
            'referer': 'https://www.9fzt.com/',
            'signature': signature,
            'timestamp': timestamp,
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Microsoft Edge";v="144"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'cross-site'
        }
        
        url = 'https://api-hq.chongnengjihua.com/finance/api/2/stock/a/rank/list'
        
        print(f"请求九方智投研报: page={page}, params={params}")
        print(f"签名: {signature}, 时间戳: {timestamp}")
        
        response = requests.get(url=url, params=params, headers=headers, timeout=15)
        print(f"响应状态码: {response.status_code}")
        
        data = response.json()
        
        if not data or 'data' not in data:
            print("没有找到 data 字段")
            return []
        
        infos = data.get('data', {}).get('infos', [])
        if not infos:
            print("没有找到研报数据")
            return []
        
        reports = []
        for item in infos:
            # 提取研报信息
            report = {
                "id": item.get('id', ''),
                "title": item.get('title', '') or item.get('prodName', '研究报告'),
                "stock_code": item.get('symbol', ''),
                "stock_name": item.get('prodName', ''),
                "publisher": item.get('publisher', '九方智投'),
                "publish_date": item.get('publishDate', datetime.now().strftime("%Y-%m-%d")),
                "rating": item.get('rating', '关注'),
                "summary": item.get('summary', '点击查看详细内容'),
                "url": item.get('url', '')
            }
            reports.append(report)
        
        print(f"获取到 {len(reports)} 条研报")
        return reports
    except Exception as e:
        print(f"获取九方智投研报错误: {e}")
        return []


@app.get("/api/research/jiufang")
async def get_jiufang_research(
    page: int = Query(1, ge=1, le=10),
    page_size: int = Query(20, ge=1, le=50)
):
    """获取九方智投最新研报"""
    try:
        reports = await fetch_jiufang_research(page, page_size)
        
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
            print("九方智投无数据，返回本地数据")
            return await get_local_research(page, page_size)
    except Exception as e:
        print(f"获取九方智投研报异常: {e}")
        return await get_local_research(page, page_size)


async def get_local_research(page: int, page_size: int):
    """从本地数据库获取研报（备用）"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        
        offset = (page - 1) * page_size
        rows = await conn.fetch("""
            SELECT r.id, r.title, r.publisher, r.publish_date, r.rating, 
                   r.summary, r.stock_code, s.stock_name
            FROM research_reports r
            LEFT JOIN stocks s ON r.stock_code = s.stock_code
            ORDER BY r.publish_date DESC
            LIMIT $1 OFFSET $2
        """, page_size, offset)
        
        reports = [
            {
                "id": row['id'],
                "title": row['title'],
                "stock_code": row['stock_code'],
                "stock_name": row['stock_name'] or row['stock_code'],
                "publisher": row['publisher'],
                "publish_date": row['publish_date'].strftime("%Y-%m-%d") if row['publish_date'] else "",
                "rating": row['rating'],
                "summary": row['summary']
            }
            for row in rows
        ]
        
        return {
            "code": 200,
            "message": "success",
            "data": reports,
            "total": len(reports),
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "database"
        }

@app.get("/api/research/latest-v2")
async def get_latest_research_v2(
    page: int = Query(1, ge=1, le=10),
    page_size: int = Query(20, ge=1, le=50)
):
    """获取最新研报（从9FZT实时爬取）"""
    try:
        reports = await fetch_latest_research_from_9fzt(page, page_size)
        
        if reports:
            return {
                "code": 200,
                "message": "success",
                "data": reports,
                "total": len(reports),
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source": "9fzt"
            }
        else:
            # 如果爬取失败，返回数据库中的模拟数据
            return await get_local_research(page, page_size)
    except Exception as e:
        logger.error(f"Get latest research error: {e}")
        return await get_local_research(page, page_size)


async def get_local_research(page: int, page_size: int):
    """从本地数据库获取研报（备用）"""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO stock_watch")
        
        offset = (page - 1) * page_size
        rows = await conn.fetch("""
            SELECT r.id, r.title, r.publisher, r.publish_date, r.rating, 
                   r.summary, r.stock_code, s.stock_name
            FROM research_reports r
            LEFT JOIN stocks s ON r.stock_code = s.stock_code
            ORDER BY r.publish_date DESC
            LIMIT $1 OFFSET $2
        """, page_size, offset)
        
        reports = [
            {
                "id": row['id'],
                "title": row['title'],
                "stock_code": row['stock_code'],
                "stock_name": row['stock_name'] or row['stock_code'],
                "publisher": row['publisher'],
                "publish_date": row['publish_date'].strftime("%Y-%m-%d") if row['publish_date'] else "",
                "rating": row['rating'],
                "summary": row['summary']
            }
            for row in rows
        ]
        
        return {
            "code": 200,
            "message": "success",
            "data": reports,
            "total": len(reports),
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "database"
        }


# 个股研报接口（增强版，优先从爬虫获取）
@app.get("/api/research/stock-v2")
async def get_stock_research_v2(
    stock_code: str,
    limit: int = Query(20, ge=1, le=50)
):
    """获取个股研报（从9FZT实时爬取）"""
    try:
        # 从9FZT搜索该股票的研报
        reports = await fetch_stock_research_from_9fzt(stock_code, limit)
        if reports:
            return {
                "code": 200,
                "message": "success",
                "data": reports,
                "source": "9fzt"
            }
    except Exception as e:
        logger.error(f"Fetch stock research error: {e}")
    
    # 回退到本地数据库
    return await get_local_stock_research(stock_code, limit)


async def fetch_stock_research_from_9fzt(stock_code: str, limit: int):
    """从9FZT搜索个股研报"""
    # 由于9FZT的研报搜索可能需要不同的接口
    # 这里先返回空，后续可根据实际接口完善
    return []


async def get_local_stock_research(stock_code: str, limit: int):
    """从本地数据库获取个股研报"""
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
