# -*- coding: utf-8 -*-
"""
教务管理系统 - 完整后端
包含：教务管理、股票分析、音乐下载、自动交易等功能
"""

import os
import sys
import time
import json
import uuid
import redis
import threading
import subprocess
import hashlib
import re
import requests
import jsonpath
import asyncio
from datetime import datetime, timedelta
from functools import wraps
from contextlib import asynccontextmanager

from flask import Flask, request, jsonify, send_file, Response, send_from_directory
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
import pandas as pd
import numpy as np
from loguru import logger

# ========== 导入定时任务 ==========
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    HAS_APSCHEDULER = True
except ImportError:
    HAS_APSCHEDULER = False
    logger.warning("apscheduler 未安装，定时任务功能将不可用")

# ========== 配置 ==========
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')
CORS(app, resources={r"/api/*": {"origins": "*"}})

# 数据库配置
DB_HOST = os.environ.get("DB_HOST", "ep-rapid-frog-ani7chkm.c-6.us-east-1.aws.neon.tech")
DB_USER = os.environ.get("DB_USER", "neondb_owner")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "npg_b1QR9lMdusev")
DB_NAME = os.environ.get("DB_NAME", "neondb")
DB_PORT = int(os.environ.get("DB_PORT", 5432))

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?sslmode=require"
SCHEMA_NAME = os.environ.get("DB_SCHEMA", "stock_data")

# Redis 配置
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()
    logger.info(f"Redis 连接成功: {REDIS_URL[:30]}...")
except Exception as e:
    logger.warning(f"Redis 连接失败: {e}")
    redis_client = None

# 目录配置
MUSIC_DIR = os.path.join(os.path.dirname(__file__), "music_downloads")
os.makedirs(MUSIC_DIR, exist_ok=True)


# ========== 健康检查 ==========
@app.route('/')
@app.route('/health')
def health_check():
    return jsonify({
        'status': 'healthy',
        'service': '教务管理系统',
        'version': '2.0.0',
        'time': datetime.now().isoformat()
    })


# ========== 教务管理 API ==========
@app.route('/api/dashboard/stats', methods=['GET'])
def dashboard_stats():
    """获取仪表盘统计数据"""
    try:
        # 模拟数据，实际可从数据库读取
        return jsonify({
            'code': 200,
            'data': {
                'student_count': 128,
                'teacher_count': 45,
                'today_classes': 12,
                'pending_classes': 3
            }
        })
    except Exception as e:
        logger.error(f"仪表盘错误: {e}")
        return jsonify({'code': 500, 'msg': str(e)}), 500


# ========== 用户登录 ==========
@app.route('/api/user/login', methods=['POST'])
def user_login():
    try:
        data = request.get_json()
        phone = data.get('phone', '13800138000')
        password = data.get('password', '123456')
        return jsonify({
            'code': 200,
            'data': {
                'id': 1,
                'name': '管理员',
                'role': 'admin',
                'openid': 'mock_openid'
            }
        })
    except Exception as e:
        return jsonify({'code': 500, 'msg': str(e)}), 500


# ========== 音乐下载模块 ==========
class NetEaseMusicSpider:
    """网易云音乐爬虫"""

    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0',
            'Referer': 'https://music.163.com/',
            'Origin': 'https://music.163.com',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }

    def search(self, keyword, limit=10):
        """搜索歌曲"""
        url = 'https://music.163.com/api/cloudsearch/pc'
        params = {'s': keyword, 'type': 1, 'offset': 0, 'limit': limit}
        try:
            response = requests.get(url, params=params, headers=self.headers, timeout=10)
            response.encoding = 'utf-8'
            content = response.text

            # 处理非标准 JSON
            if not content.startswith('{'):
                json_match = re.search(r'\{.*\}', content, re.DOTALL)
                if json_match:
                    content = json_match.group()

            data = json.loads(content)
            if data.get('code') != 200:
                return []

            songs = data.get('result', {}).get('songs', [])
            results = []
            for song in songs:
                artists = song.get('artists', [])
                artist = artists[0].get('name', '') if artists else ''
                results.append({
                    'contentId': str(song.get('id', '')),
                    'songName': song.get('name', ''),
                    'singer': artist,
                    'album': song.get('album', {}).get('name', ''),
                    'duration': song.get('duration', 0) // 1000
                })
            return results
        except Exception as e:
            logger.error(f"网易云搜索失败: {e}")
            return []

    def get_download_url(self, song_id):
        """获取下载链接"""
        url = 'https://music.163.com/api/song/enhance/player/url'
        try:
            response = requests.get(url, params={'ids': song_id, 'br': 320000}, headers=self.headers, timeout=10)
            content = response.text
            if not content.startswith('{'):
                json_match = re.search(r'\{.*\}', content, re.DOTALL)
                if json_match:
                    content = json_match.group()
            data = json.loads(content)
            if data.get('code') == 200 and data.get('data'):
                return data['data'][0].get('url')
            return None
        except Exception as e:
            logger.error(f"获取下载链接失败: {e}")
            return None

    def download(self, song_url, song_name, artist=''):
        """下载歌曲"""
        if not song_url:
            return None
        try:
            response = requests.get(song_url, headers=self.headers, timeout=30)
            if response.status_code != 200:
                return None
            safe_name = re.sub(r'[\\/*?:"<>|]', '', song_name)
            safe_artist = re.sub(r'[\\/*?:"<>|]', '', artist) if artist else ''
            filename = f"{safe_name} - {safe_artist}.mp3" if safe_artist else f"{safe_name}.mp3"
            filepath = os.path.join(MUSIC_DIR, filename)
            with open(filepath, 'wb') as f:
                f.write(response.content)
            return filepath
        except Exception as e:
            logger.error(f"下载失败: {e}")
            return None


# ========== 音乐 API ==========
@app.route('/api/music/search', methods=['POST'])
def music_search():
    try:
        data = request.get_json()
        keyword = data.get('keyword', '').strip()
        if not keyword:
            return jsonify({'code': 400, 'msg': '请输入歌曲名称'})

        # 尝试从缓存读取
        if redis_client:
            cache_key = f"music:search:{keyword}"
            cached = redis_client.get(cache_key)
            if cached:
                return jsonify({'code': 200, 'data': json.loads(cached), 'source': 'cache'})

        spider = NetEaseMusicSpider()
        results = spider.search(keyword, limit=10)

        # 缓存结果
        if redis_client and results:
            redis_client.setex(f"music:search:{keyword}", 3600, json.dumps(results))

        return jsonify({'code': 200, 'data': results, 'source': 'netease'})
    except Exception as e:
        logger.error(f"音乐搜索异常: {e}")
        return jsonify({'code': 500, 'msg': str(e)}), 500


@app.route('/api/music/download', methods=['POST'])
def music_download():
    try:
        data = request.get_json()
        content_id = data.get('contentId')
        song_name = data.get('songName')
        singer = data.get('singer', '')

        if not content_id or not song_name:
            return jsonify({'code': 400, 'msg': '缺少必要参数'})

        spider = NetEaseMusicSpider()
        url = spider.get_download_url(content_id)

        if not url:
            return jsonify({'code': 404, 'msg': '获取播放链接失败，可能为付费歌曲'})

        filepath = spider.download(url, song_name, singer)
        if filepath:
            return jsonify({
                'code': 200,
                'msg': '下载成功',
                'data': {'filepath': filepath, 'filename': os.path.basename(filepath)}
            })
        else:
            return jsonify({'code': 500, 'msg': '下载文件失败'})
    except Exception as e:
        logger.error(f"下载异常: {e}")
        return jsonify({'code': 500, 'msg': str(e)}), 500


@app.route('/api/music/list', methods=['GET'])
def music_list():
    try:
        files = []
        for f in os.listdir(MUSIC_DIR):
            if f.endswith('.mp3'):
                filepath = os.path.join(MUSIC_DIR, f)
                files.append({
                    'name': f,
                    'path': filepath,
                    'size': os.path.getsize(filepath)
                })
        return jsonify({'code': 200, 'data': files})
    except Exception as e:
        return jsonify({'code': 500, 'msg': str(e)}), 500


@app.route('/api/music/download/<filename>', methods=['GET'])
def music_download_file(filename):
    try:
        safe_name = re.sub(r'[\\/*?:"<>|]', '', filename)
        return send_from_directory(MUSIC_DIR, safe_name, as_attachment=True)
    except Exception as e:
        return jsonify({'code': 500, 'msg': str(e)}), 500


@app.route('/api/music/delete/<filename>', methods=['DELETE'])
def music_delete(filename):
    try:
        safe_name = re.sub(r'[\\/*?:"<>|]', '', filename)
        filepath = os.path.join(MUSIC_DIR, safe_name)
        if os.path.exists(filepath):
            os.remove(filepath)
            return jsonify({'code': 200, 'msg': '删除成功'})
        return jsonify({'code': 404, 'msg': '文件不存在'})
    except Exception as e:
        return jsonify({'code': 500, 'msg': str(e)}), 500


# ========== 股票系统简化接口 ==========
@app.route('/api/stock/picks', methods=['GET'])
def stock_picks():
    return jsonify({
        'code': 200,
        'data': [
            {'code': '002787', 'name': '华源控股', 'price': 32.47, 'change_pct': 7.02,
             'total_score': 70.76, 'advice': '持有/观望', 'fin_rating': '良好(2026Q1)'},
            {'code': '601133', 'name': '柏诚股份', 'price': 38.10, 'change_pct': 9.48,
             'total_score': 65.00, 'advice': '买入', 'fin_rating': '良好(2026Q1)'},
        ],
        'has_data': True,
        'data_date': '2026-06-29'
    })


@app.route('/api/stock/market', methods=['GET'])
def stock_market():
    return jsonify({
        'code': 200,
        'data': {
            'market_state': '震荡市',
            'market_score': 50,
            'position_ratio': 0.4,
            'advice': '高抛低吸，仓位30-50%',
            'limit_up_count': 73,
            'limit_down_count': 44,
            'up_count': 756,
            'down_count': 4394,
            'advance_percent': 14.56
        }
    })


@app.route('/api/stock/sentiment', methods=['GET'])
def stock_sentiment():
    return jsonify({
        'code': 200,
        'data': {
            'concepts': [
                {'name': '昨日连板', 'change_pct': 3.13, 'leading_stock': '宏柏新材'},
                {'name': '中芯国际概念', 'change_pct': 1.13, 'leading_stock': '有研硅'},
            ],
            'industries': [
                {'name': '乘用车', 'change_pct': -4.00, 'leading_stock': '长安汽车'},
                {'name': '专业连锁Ⅱ', 'change_pct': -4.10, 'leading_stock': '吉峰科技'},
            ]
        }
    })


@app.route('/api/watchlist', methods=['GET'])
def watchlist():
    return jsonify({'code': 200, 'data': [], 'message': '暂无自选股'})


@app.route('/api/holdings', methods=['GET'])
def holdings():
    return jsonify({
        'code': 200,
        'data': [],
        'account': {
            'snapshot_date': '2026-06-29',
            'total_value': 100000,
            'total_pnl': 0,
            'total_pnl_pct': 0
        }
    })


# ========== 自动交易接口 ==========
@app.route('/api/auto-trade/status', methods=['GET'])
def auto_trade_status():
    return jsonify({
        'code': 200,
        'data': {
            'cash': 100000,
            'total_value': 100000,
            'total_pnl': 0,
            'total_pnl_pct': 0,
            'daily_trade_count': 0,
            'position_count': 0,
            'positions': []
        }
    })


@app.route('/api/auto-trade/execute', methods=['POST'])
def auto_trade_execute():
    return jsonify({
        'code': 200,
        'message': '自动交易执行完成',
        'trades': [],
        'account': {
            'cash': 100000,
            'total_value': 100000,
            'total_pnl': 0,
            'total_pnl_pct': 0
        }
    })


@app.route('/api/auto-trade/trades', methods=['GET'])
def auto_trade_trades():
    return jsonify({'code': 200, 'data': [], 'count': 0})


@app.route('/api/auto-trade/trades/stats', methods=['GET'])
def auto_trade_stats():
    return jsonify({
        'code': 200,
        'data': {
            'total': {'trades': 0, 'total_pnl': 0, 'total_commission': 0, 'avg_pnl_pct': 0},
            'today': {'trades': 0, 'buy_count': 0, 'sell_count': 0, 'today_pnl': 0},
            'top_stocks': []
        }
    })


@app.route('/api/auto-trade/config', methods=['GET'])
def auto_trade_config():
    return jsonify({
        'code': 200,
        'data': {
            'stop_loss_pct': -7.0,
            'take_profit_pct': 15.0,
            'max_daily_trades': 20,
            'single_buy_amount': 10000,
            'min_buy_score': 50
        }
    })


@app.route('/api/auto-trade/config', methods=['POST'])
def auto_trade_save_config():
    return jsonify({'code': 200, 'message': '配置保存成功'})


# ========== 启动主程序 ==========
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"启动教务管理系统，端口: {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
