"""九方智投数据采集模块 - 多线程加速版

使用 chongnengjihua.com API 获取实时行情、行业板块和概念板块数据。
支持多线程并行请求，大幅提升采集速度。
"""

import os
import time
import json
import hashlib
from datetime import date
from typing import Dict, List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import requests
import pandas as pd
from loguru import logger

from config import DATA_SOURCES, STOCK_FILTER, DATA_CACHE_DIR, PERFORMANCE, LLM_CONFIG

# API 基础地址
API_BASE = "https://api-hq.chongnengjihua.com/finance/api/2/stock/a/rank/list"
SECTOR_API = "https://hq.chongnengjihua.com/rjhy-quote-sector/api/1/pc/plate/block/quote/list"
SIGN_SALT = "sjdxfnqogbzoun13d971ckh8p"


def _gen_updown_sign(timestamp: str) -> str:
    """
    为涨跌分布接口生成签名
    """
    sign_salt = "sjdxfnqogbzoun13d971ckh8p"
    base = f"{sign_salt}{timestamp}"
    return hashlib.md5(base.encode()).hexdigest()

def _gen_stock_sign(listed_sector: str, sort_field: str, sort_type: str,
                    timestamp: str, page: int) -> str:
    base = f"{SIGN_SALT}{listed_sector}{page}20{sort_field}{sort_type}{timestamp}"
    return hashlib.md5(base.encode()).hexdigest()

def _gen_sector_sign(timestamp: str) -> str:
    return hashlib.md5(f"{SIGN_SALT}{timestamp}".encode()).hexdigest()

def call_llm_analysis(stock_data: pd.DataFrame, sentiment: dict) -> Optional[str]:
    """
    调用 LLM 对选股结果进行分析
    stock_data: 精选股票 DataFrame
    sentiment: 市场情绪字典
    """
    if not LLM_CONFIG.get("enable_llm", False):
        return None

    if stock_data.empty:
        return None

    # 调试：打印列名
    # logger.info(f"LLM 分析接收到的列名: {stock_data.columns.tolist()}")

    try:
        import requests as req

        # 准备股票数据文本
        stock_text_lines = []
        for _, row in stock_data.head(LLM_CONFIG.get("max_llm_stocks", 10)).iterrows():
            stock_text_lines.append(
                f"- {row['name']}({row['code']}): 现价{row['price']:.2f}, "
                f"涨跌幅{row['change_pct']:.2f}%, 量比{row.get('volume_ratio', 0):.2f}, "
                f"主力净流入{row.get('main_inflow', 0):.2f}亿, "
                f"RSI{row.get('rsi', 0):.1f}, 综合评分{row.get('total_score', 0):.0f}"  # 这里使用 .get()
            )

        prompt = f"""你是一位专业的A股量化分析师。请基于以下今日选股数据，给出简要分析：

【选股数据】（共{len(stock_data)}只）
{chr(10).join(stock_text_lines)}

【市场情绪】
情绪：{sentiment.get('mood', '中性')}（评分{sentiment.get('mood_score', 50)}）
热点概念：{sentiment.get('top_concept', 'N/A')}
热点行业：{sentiment.get('top_industry', 'N/A')}

请按以下格式输出分析报告：
### 今日市场概况
（简要描述市场情绪和热点方向，50字以内）

### 重点个股点评
（对评分最高的3只股票逐一分析，每只30字以内）

### 风险提示
（结合技术指标和市场情绪，提示可能的风险，30字以内）

### 操作建议
（给出总体操作建议，20字以内）"""

        headers = {
            "Authorization": f"Bearer {LLM_CONFIG['api_key']}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": LLM_CONFIG['model'],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": LLM_CONFIG.get('temperature', 0.3),
            "max_tokens": LLM_CONFIG.get('max_tokens', 1024)
        }

        response = req.post(
            f"{LLM_CONFIG['base_url']}/chat/completions",
            json=payload,
            headers=headers,
            timeout=30
        )

        if response.status_code == 200:
            result = response.json()
            return result['choices'][0]['message']['content']
        else:
            logger.warning(f"LLM API 调用失败: {response.status_code}")
            return None

    except Exception as e:
        logger.error(f"LLM 分析异常: {e}")
        return None


class JiuFangCollector:
    """九方智投数据采集器（多线程加速版）"""

    def __init__(self):
        self.config = DATA_SOURCES.get("jiufang", {})
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
        })
        self.interval = self.config.get("request_interval", 1)
        # 缓存目录
        self._cache_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            DATA_CACHE_DIR,
        )
        self._today = date.today().isoformat()
        self._results_lock = threading.Lock()

    # ========== 缓存相关 ==========

    def _cache_path(self, stem: str, ext: str) -> str:
        os.makedirs(self._cache_dir, exist_ok=True)
        return os.path.join(self._cache_dir, f"{stem}_{self._today}.{ext}")

    def has_today_cache(self) -> bool:
        return os.path.exists(self._cache_path("stocks", "pkl"))

    def save_full_cache(self, stocks: pd.DataFrame, concepts: pd.DataFrame,
                        industries: pd.DataFrame) -> None:
        if stocks.empty:
            logger.warning("stocks 为空，跳过缓存保存")
            return
        stocks.to_pickle(self._cache_path("stocks", "pkl"))
        concepts.to_pickle(self._cache_path("concepts", "pkl"))
        industries.to_pickle(self._cache_path("industries", "pkl"))
        logger.info(f"数据已缓存至 {self._cache_dir} ({self._today})")

    def load_full_cache(self) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        stocks_path = self._cache_path("stocks", "pkl")
        concepts_path = self._cache_path("concepts", "pkl")
        industries_path = self._cache_path("industries", "pkl")

        if not os.path.exists(stocks_path):
            raise FileNotFoundError(f"缓存文件不存在: {stocks_path}")

        stocks = pd.read_pickle(stocks_path)
        concepts = pd.read_pickle(concepts_path)
        industries = pd.read_pickle(industries_path)
        logger.info(f"从缓存加载数据: {len(stocks)} 只个股, "
                    f"{len(concepts)} 个概念, {len(industries)} 个行业")
        return stocks, concepts, industries

    def get_market_stats(self) -> dict:
        """
        通过九方智投涨跌分布接口获取全市场统计数据
        返回: {
            'total': 总股票数,
            'up_count': 上涨家数,
            'down_count': 下跌家数,
            'flat_count': 平盘家数,
            'limit_up_count': 涨停家数,
            'limit_down_count': 跌停家数,
            'advance_percent': 上涨比例
        }
        """
        timestamp = str(int(time.time() * 1000))
        headers = {
            "accept": "*/*",
            "origin": "https://www.9fzt.com",
            "referer": "https://www.9fzt.com/",
            "signature": _gen_updown_sign(timestamp),
            "timestamp": timestamp,
            "user-agent": self.session.headers["User-Agent"],
        }
        url = "https://api-hq.chongnengjihua.com/finance/api/1/stock/up/down/distributed"

        try:
            resp = self.session.get(url, headers=headers, timeout=10)
            data = resp.json()

            # 九方智投接口成功时 code 为 1
            if data.get('code') == 1 and data.get('data'):
                result = data['data']
                up_count = result.get('up', 0)
                down_count = result.get('down', 0)
                flat_count = result.get('flat', 0)
                total = up_count + down_count + flat_count
                limit_up_count = result.get('upLimit', 0)
                limit_down_count = result.get('downLimit', 0)
                advance_percent = up_count / total if total > 0 else 0

                logger.info(f"九方智投全市场统计: 总数={total}, 上涨={up_count}({advance_percent:.1%}), "
                            f"下跌={down_count}, 涨停={limit_up_count}, 跌停={limit_down_count}")

                return {
                    'total': total,
                    'up_count': up_count,
                    'down_count': down_count,
                    'flat_count': flat_count,
                    'limit_up_count': limit_up_count,
                    'limit_down_count': limit_down_count,
                    'advance_percent': advance_percent,
                }
            else:
                logger.warning(f"九方智投涨跌分布接口返回异常: {data}")
                return self._get_empty_stats()
        except Exception as e:
            logger.error(f"获取九方智投市场统计数据失败: {e}")
            return self._get_empty_stats()

    def _get_empty_stats(self) -> dict:
        """返回一个空的市场统计数据字典"""
        return {
            'total': 0,
            'up_count': 0,
            'down_count': 0,
            'flat_count': 0,
            'limit_up_count': 0,
            'limit_down_count': 0,
            'advance_percent': 0,
        }



    # ========== 个股行情（多线程） ==========

    def _fetch_stock_page(self, sort_type: str, page: int) -> List[Tuple]:
        """获取单页个股数据（供多线程调用）"""
        timestamp = str(int(time.time() * 1000))
        params = {
            "pageNum": str(page),
            "pageSize": "20",
            "listedSector": "0",
            "sortField": "pxChangeRate",
            "sortType": sort_type,
        }
        headers = {
            "accept": "application/json, text/plain, */*",
            "origin": "https://www.9fzt.com",
            "referer": "https://www.9fzt.com/",
            "signature": _gen_stock_sign(
                params["listedSector"], params["sortField"],
                sort_type, timestamp, page,
            ),
            "timestamp": timestamp,
            "user-agent": self.session.headers["User-Agent"],
        }
        try:
            resp = self.session.get(API_BASE, params=params, headers=headers, timeout=15)
            data = resp.json()
            infos = data.get("data", {}).get("infos")
            if not infos:
                return []

            page_results = []
            for item in infos:
                if item.get("symbol") is None or item.get("lastPx") is None:
                    continue
                page_results.append((
                    item["symbol"],
                    item.get("prodName", ""),
                    item.get("openPrice") or 0,
                    item.get("highPx") or 0,
                    item.get("lowPx") or 0,
                    item["lastPx"],
                    item.get("closePx") or 0,
                    item.get("preClosePx") or 0,
                    (item.get("pxChangeRate") or 0) * 100,
                    item.get("mainNetFlow") or 0,
                    (item.get("turnoverRatio") or 0) * 100,
                    item.get("volRatio") or 0,
                    item.get("businessBalance") or 0,
                    (item.get("amplitude") or 0) * 100,
                    (item.get("day5PxChangeRate") or 0) * 100,
                    item.get("marketValue") or 0,
                    item.get("circulationValue") or 0,
                    item.get("peRate") or 0,
                ))
            return page_results
        except Exception as e:
            logger.warning(f"个股排行请求失败 (page={page}, sort={sort_type}): {e}")
            return []

    def _fetch_stock_rank_parallel(self, sort_type: str, pages: int) -> List[Tuple]:
        """多线程获取涨跌幅排行榜"""
        if pages <= 0:
            return []

        thread_count = PERFORMANCE.get("parallel_workers", 5)
        all_results = []

        with ThreadPoolExecutor(max_workers=thread_count) as executor:
            futures = {executor.submit(self._fetch_stock_page, sort_type, page): page
                       for page in range(1, pages + 1)}

            for future in as_completed(futures):
                page_num = futures[future]
                try:
                    page_results = future.result(timeout=30)
                    with self._results_lock:
                        all_results.extend(page_results)
                    logger.debug(f"个股 {sort_type} 榜第 {page_num} 页完成，获取 {len(page_results)} 条")
                except Exception as e:
                    logger.warning(f"个股第 {page_num} 页获取失败: {e}")

        logger.info(f"个股 {sort_type} 榜采集完成，共 {len(all_results)} 条")
        return all_results

    def _fetch_stock_rank(self, sort_type: str, pages: int = 2) -> List[Tuple]:
        """获取涨跌幅排行榜（兼容单线程调用，内部自动选择多线程）"""
        if pages > 10:
            return self._fetch_stock_rank_parallel(sort_type, pages)
        else:
            # 页数少时使用单线程
            results = []
            for page in range(1, pages + 1):
                page_results = self._fetch_stock_page(sort_type, page)
                results.extend(page_results)
                time.sleep(self.interval)
            return results

    def get_hot_stocks(self, stock_pages: int = None) -> pd.DataFrame:
        """获取热门个股（涨幅榜+跌幅榜合并）"""
        if stock_pages is None:
            stock_pages = PERFORMANCE.get("stock_pages", 100)
        logger.info("正在从九方智投获取热门个股行情...")

        # 涨幅榜：多线程
        up_list = self._fetch_stock_rank("0", pages=stock_pages)
        # 跌幅榜：单线程即可
        down_list = self._fetch_stock_rank("1", pages=2)

        all_stocks = up_list + down_list

        if not all_stocks:
            logger.warning("九方智投未返回个股数据")
            return pd.DataFrame()

        seen = set()
        records = []
        for symbol, name, openPrice, highPx, lowPx, price, closePx, preClosePx, \
            change_pct, mainNetFlow, turnoverRatio, volRatio, businessBalance, \
            amplitude, day5PxChangeRate, marketValue, circulationValue, peRate in all_stocks:
            if symbol in seen:
                continue
            seen.add(symbol)

            _price = float(price)
            _pre_close = float(preClosePx)
            _change_pct = float(change_pct)
            _biz_balance = float(businessBalance)
            _main_flow = float(mainNetFlow)
            _mkt_value = float(marketValue)
            _circ_value = float(circulationValue)

            change_amt = round(_pre_close * _change_pct / 100, 2)
            estimated_volume = int(round(_biz_balance / _price)) if _price > 0 else 0

            records.append({
                "code": symbol,
                "name": name,
                "price": _price,
                "change_pct": _change_pct,
                "change_amount": change_amt,
                "volume": estimated_volume,
                "amount": _biz_balance / 1e8,
                "turnover_rate": float(turnoverRatio),
                "pe_ratio": float(peRate),
                "high": float(highPx),
                "low": float(lowPx),
                "open": float(openPrice),
                "pre_close": _pre_close,
                "market_cap": _mkt_value / 1e8,
                "circulating_market_cap": _circ_value / 1e8,
                "volume_ratio": float(volRatio),
                "amplitude": float(amplitude),
                "main_inflow": _main_flow / 1e8,
                "main_inflow_pct": round(_main_flow / _biz_balance * 100, 2) if _biz_balance else 0,
            })

        df = pd.DataFrame(records)
        if len(df) > 1:
            df["heat_score"] = df["change_pct"].abs().rank(pct=True) * 100
            df = df.sort_values("heat_score", ascending=False)
        else:
            df["heat_score"] = 50

        logger.info(f"九方智投: 获取到 {len(df)} 只热门个股")
        return df

    # ========== 板块成分股 ==========

    @staticmethod
    def get_HY_OR_GN_stock_info(hqTypeCode, prodCode):
        """获取每个板块或概念里的个股信息"""
        all_codes = []
        for page in range(1, 10):
            timestamp = str(int(time.time() * 1000))
            params = {
                'hqTypeCode': hqTypeCode,
                'prodCode': prodCode,
                'sortFlag': 'true',
                'sortFields': 'countBoard',
                'simPageSize': 'yes',
                'pageSize': '20',
                'pageNum': page,
            }
            sign = _gen_sector_sign(timestamp)
            headers = {
                'accept': 'application/json, text/plain, */*',
                'origin': 'https://stock.9fzt.com',
                'referer': 'https://stock.9fzt.com/',
                'signature': sign,
                'timestamp': timestamp,
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            }
            url = 'https://hq.chongnengjihua.com/rjhy-quote-sector/api/1/pc/plate/block/stock/group'
            try:
                response = requests.get(url=url, headers=headers, params=params, timeout=10)
                data = response.json().get('data', {}).get('sortProdGrp', [])
                if not data:
                    break
                for item in data:
                    all_codes.append(item['prodCode'])
            except Exception as e:
                logger.warning(f"获取板块成分股失败 page={page}: {e}")
                break
        return ','.join(all_codes)

    # ========== 板块行情（多线程） ==========

    def _fetch_sector_page(self, hq_type_code: str, page: int) -> List[Tuple]:
        """获取单页板块数据（供多线程调用）"""
        timestamp = str(int(time.time() * 1000))
        params = {
            "hqTypeCode": hq_type_code,
            "sortFlag": "true",
            "sortFields": "pxChangeRate",
            "pageNum": page,
            "pageSize": "30",
        }
        headers = {
            "accept": "application/json, text/plain, */*",
            "origin": "https://stock.9fzt.com",
            "referer": "https://stock.9fzt.com/",
            "signature": _gen_sector_sign(timestamp),
            "timestamp": timestamp,
            "user-agent": self.session.headers["User-Agent"],
        }
        try:
            resp = self.session.get(SECTOR_API, params=params, headers=headers, timeout=15)
            data = resp.json()
            plates = data.get("data", {}).get("plate")
            if not plates:
                return []

            page_results = []
            for item in plates:
                stock_prodCodes = self.get_HY_OR_GN_stock_info(hq_type_code, item['ProdCode'])
                leadingStockName = item['RiseFirstGrp'][0]['ProdName']
                leadingStockCode = item['RiseFirstGrp'][0]['ProdCode']
                stockCount = item['RiseCount'] + item['FallCount'] + item['FlatCount']
                upCount = item['RiseCount']
                UpLimitNum = item.get('UpLimitNum', 0) or 0
                Fundflow = (item.get('Fundflow', 0) or 0) / 1e8
                page_results.append((
                    item["ProdCode"],
                    item["ProdName"],
                    item["LastPx"] / 1000,
                    item["PxChangeRate"] / 100,
                    leadingStockName,
                    leadingStockCode,
                    stockCount,
                    upCount,
                    UpLimitNum,
                    Fundflow,
                    stock_prodCodes
                ))
            return page_results
        except Exception as e:
            logger.warning(f"板块请求失败 (page={page}, type={hq_type_code}): {e}")
            return []

    def _fetch_sector_parallel(self, hq_type_code: str, pages: int) -> List[Tuple]:
        """多线程获取板块数据"""
        if pages <= 0:
            return []

        thread_count = PERFORMANCE.get("parallel_workers", 3)
        all_results = []

        with ThreadPoolExecutor(max_workers=thread_count) as executor:
            futures = {executor.submit(self._fetch_sector_page, hq_type_code, page): page
                       for page in range(1, pages + 1)}

            for future in as_completed(futures):
                page_num = futures[future]
                try:
                    page_results = future.result(timeout=60)
                    with self._results_lock:
                        all_results.extend(page_results)
                    logger.debug(f"板块 {hq_type_code} 第 {page_num} 页完成，获取 {len(page_results)} 条")
                except Exception as e:
                    logger.warning(f"板块第 {page_num} 页获取失败: {e}")

        return all_results

    def _fetch_sector(self, hq_type_code: str, pages: int = None) -> List[Tuple]:
        """获取行业/概念板块原始数据（HY=行业, GN=概念）"""
        if pages is None:
            if hq_type_code == "HY":
                pages = PERFORMANCE.get("industry_pages", 3)
            else:
                pages = PERFORMANCE.get("concept_pages", 5)

        # 使用多线程获取
        return self._fetch_sector_parallel(hq_type_code, pages)

    def get_hot_concepts(self, concept_pages: int = None) -> pd.DataFrame:
        """获取概念板块排行"""
        logger.info("正在从九方智投获取概念板块数据...")
        raw = self._fetch_sector("GN", pages=concept_pages)
        if not raw:
            logger.warning("九方智投未返回概念板块数据")
            return pd.DataFrame()

        records = [
            {
                "concept_name": name,
                "concept_code": code,
                "change_pct": float(pct),
                "price": float(price),
                "heat_rank": i + 1,
                "leading_stock": leading_name,
                "leading_stock_code": leading_code,
                "stock_count": int(stock_count) if stock_count else 0,
                "up_count": int(up_count) if up_count else 0,
                "UpLimitNum": int(UpLimitNum) if UpLimitNum else 0,
                "Fundflow": float(Fundflow) if Fundflow else 0.0,
                "stock_prodCodes": stock_prodCodes
            }
            for i, (code, name, price, pct, leading_name, leading_code,
                    stock_count, up_count, UpLimitNum, Fundflow, stock_prodCodes) in enumerate(raw)
        ]
        df = pd.DataFrame(records)
        logger.info(f"九方智投: 获取到 {len(df)} 个概念板块")
        return df

    def get_hot_industry(self, industry_pages: int = None) -> pd.DataFrame:
        """获取行业板块排行"""
        logger.info("正在从九方智投获取行业板块数据...")
        raw = self._fetch_sector("HY", pages=industry_pages)
        if not raw:
            logger.warning("九方智投未返回行业板块数据")
            return pd.DataFrame()

        records = [
            {
                "industry_name": name,
                "change_pct": float(pct),
                "heat_rank": i + 1,
                "leading_stock": leading_name,
                "leading_stock_code": leading_code,
                "stock_count": int(stock_count) if stock_count else 0,
                "up_count": int(up_count) if up_count else 0,
                "UpLimitNum": int(UpLimitNum) if UpLimitNum else 0,
                "Fundflow": float(Fundflow) if Fundflow else 0.0,
                "stock_prodCodes": stock_prodCodes
            }
            for i, (code, name, price, pct, leading_name, leading_code,
                    stock_count, up_count, UpLimitNum, Fundflow, stock_prodCodes) in enumerate(raw)
        ]
        df = pd.DataFrame(records)
        logger.info(f"九方智投: 获取到 {len(df)} 个行业板块")
        return df

    def get_market_sentiment(self) -> dict:
        """计算市场情绪指标"""
        concepts = self.get_hot_concepts()
        industries = self.get_hot_industry()
        return self._calc_sentiment(concepts, industries)

    @staticmethod
    def _calc_sentiment(concepts: pd.DataFrame, industries: pd.DataFrame) -> dict:
        avg_c = concepts["change_pct"].mean() if not concepts.empty else 0
        avg_i = industries["change_pct"].mean() if not industries.empty else 0

        if avg_c > 2:
            mood, score = "积极", 80
        elif avg_c > 0.5:
            mood, score = "偏积极", 65
        elif avg_c > -0.5:
            mood, score = "中性", 50
        elif avg_c > -2:
            mood, score = "偏谨慎", 35
        else:
            mood, score = "谨慎", 20

        return {
            "mood": mood,
            "mood_score": score,
            "hot_concept_count": len(concepts),
            "hot_industry_count": len(industries),
            "avg_concept_change": avg_c,
            "avg_industry_change": avg_i,
            "top_concept": concepts.iloc[0]["concept_name"] if not concepts.empty else "N/A",
            "top_industry": industries.iloc[0]["industry_name"] if not industries.empty else "N/A",
        }

    # ========== 自选股 ==========

    def _build_record(self, row: tuple) -> dict:
        """将九方 API 原始行转为标准记录字典"""
        symbol, name, openPrice, highPx, lowPx, price, closePx, preClosePx, \
            change_pct, mainNetFlow, turnoverRatio, volRatio, businessBalance, \
            amplitude, day5PxChangeRate, marketValue, circulationValue, peRate = row

        _price = float(price)
        _pre_close = float(preClosePx)
        _change_pct = float(change_pct)
        _biz_balance = float(businessBalance)
        _main_flow = float(mainNetFlow)
        _mkt_value = float(marketValue)
        _circ_value = float(circulationValue)

        change_amt = round(_pre_close * _change_pct / 100, 2)
        estimated_volume = int(round(_biz_balance / _price)) if _price > 0 else 0

        return {
            "code": symbol,
            "name": name,
            "price": _price,
            "change_pct": _change_pct,
            "change_amount": change_amt,
            "volume": estimated_volume,
            "amount": _biz_balance / 1e8,
            "turnover_rate": float(turnoverRatio),
            "pe_ratio": float(peRate),
            "high": float(highPx),
            "low": float(lowPx),
            "open": float(openPrice),
            "pre_close": _pre_close,
            "market_cap": _mkt_value / 1e8,
            "circulating_market_cap": _circ_value / 1e8,
            "volume_ratio": float(volRatio),
            "amplitude": float(amplitude),
            "main_inflow": _main_flow / 1e8,
            "main_inflow_pct": round(_main_flow / _biz_balance * 100, 2) if _biz_balance else 0,
        }

    def get_stocks_by_codes(self, codes: List[str], stock_pages: int = None) -> pd.DataFrame:
        """按股票代码查询指定股票行情"""
        if stock_pages is None:
            stock_pages = PERFORMANCE.get("stock_pages", 100)
        codes = [str(c).strip() for c in codes]
        code_set = set(codes)
        logger.info(f"正在从九方智投查询 {len(codes)} 只股票...")
        matched = []
        seen = set()

        up_list = self._fetch_stock_rank("0", pages=stock_pages)
        if up_list:
            for row in up_list:
                symbol = row[0]
                if symbol in code_set and symbol not in seen:
                    seen.add(symbol)
                    matched.append(self._build_record(row))

        remaining = code_set - seen
        if remaining:
            logger.info(f"涨幅榜未找到 {len(remaining)} 只，从跌幅榜补充查询...")
            down_list = self._fetch_stock_rank("1", pages=2)
            if down_list:
                for row in down_list:
                    symbol = row[0]
                    if symbol in remaining and symbol not in seen:
                        seen.add(symbol)
                        matched.append(self._build_record(row))

        not_found = code_set - seen
        if not_found:
            logger.warning(f"以下股票未找到（停牌/未上市）：{not_found}")

        df = pd.DataFrame(matched)
        if not df.empty:
            logger.info(f"股票查询完成: 匹配 {len(df)}/{len(codes)} 只")
        return df


# 导出 LLM 分析函数供外部使用
__all__ = ['JiuFangCollector', 'call_llm_analysis']
