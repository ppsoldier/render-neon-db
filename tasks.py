cat > tasks.py << 'EOF'
# tasks.py
from celery_app import app
from celery import Task
import requests
import re
import os
import uuid
import subprocess
import time
import json
import redis
import base64
from datetime import datetime
from requests.exceptions import ChunkedEncodingError, ConnectionError, Timeout

# ========== 任务状态存储 ==========
_task_status_store = {}
_task_result_store = {}

def set_task_status(task_id, status):
    _task_status_store[task_id] = status

def get_task_status(task_id):
    return _task_status_store.get(task_id, {'status': 'not_found'})

def set_task_result(task_id, result):
    _task_result_store[task_id] = result

def get_task_result(task_id):
    return _task_result_store.get(task_id)

def store_video_to_redis(task_id, file_path):
    """将视频文件存储到 Redis"""
    try:
        redis_url = os.environ.get('REDIS_URL')
        if not redis_url:
            print("REDIS_URL 未设置，跳过存储")
            return False
        r = redis.from_url(redis_url)
        with open(file_path, 'rb') as f:
            file_data = base64.b64encode(f.read()).decode('utf-8')
        r.setex(f'video:{task_id}', 3600, file_data)
        print(f"视频已存储到 Redis: {task_id}, 大小: {len(file_data)} bytes")
        return True
    except Exception as e:
        print(f"存储到 Redis 失败: {e}")
        return False


# ========== B站请求配置 ==========
BILI_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://www.bilibili.com/',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

BILI_COOKIES = {
    'buvid3': 'E1AC41CE-8298-B4E8-2B11-711D83FCEB4D07978infoc',
    'b_nut': '1774395407',
    'bsource': 'search_bing',
    '_uuid': '696B6B59-DCBB-93710-106A9-2F84CAC10BCA957649infoc',
    'home_feed_column': '5',
    'browser_resolution': '1912-956',
    'buvid4': '4DB22FA4-9C1B-3FF9-09D1-FC56BA6029AA09187-026032507-s5Db6HTmRJMFxI7gzVHFJA%3D%3D',
    'buvid_fp': '82d07d9422c9ad7c67f3c53cc409b7e8',
    'bmg_af_switch': '1',
    'bmg_src_def_domain': 'i2.hdslb.com',
    'bili_ticket': 'eyJhbGciOiJIUzI1NiIsImtpZCI6InMwMyIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3NzQ2NTQ2MjQsImlhdCI6MTc3NDM5NTM2NCwicGx0IjotMX0.KbRAOmZjhvBQ7MrPSaVnGd_5qhwK_WyiYs25aY5WJ5I',
    'bili_ticket_expires': '1774654564',
    'sid': '83jnmdei',
    'CURRENT_QUALITY': '0',
    'rpdid': "|(umR~lRuRlJ0J'u~~RJmlRRu",
    'CURRENT_FNVAL': '4048',
    'b_lsid': 'C9BCD791_19D225F6A76',
}


def parse_video_page(url):
    """解析 B 站视频页面"""
    bv_match = re.search(r'BV([a-zA-Z0-9]+)', url)
    if not bv_match:
        return None
    bvid = bv_match.group(0)
    print(f"解析 BV 号: {bvid}")

    try:
        api_url = f'https://api.bilibili.com/x/web-interface/view?bvid={bvid}'
        resp = requests.get(api_url, headers=BILI_HEADERS, cookies=BILI_COOKIES, timeout=10)
        data = resp.json()
        if data.get('code') != 0:
            return None
        video_data = data['data']
        title = video_data.get('title', '未知标题')
        cid = video_data.get('cid')
        play_url = f'https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&qn=80&fnval=16&fourk=1'
        play_resp = requests.get(play_url, headers=BILI_HEADERS, cookies=BILI_COOKIES, timeout=10)
        play_data = play_resp.json()
        if play_data.get('code') != 0:
            return None
        dash = play_data['data'].get('dash', {})
        video_url = dash['video'][0].get('baseUrl') if dash.get('video') else None
        audio_url = dash['audio'][0].get('baseUrl') if dash.get('audio') else None
        if not video_url:
            return None
        title = title.replace(' ', '').replace('|', '').replace("'", '').replace('/', '_')
        return {'title': title, 'video_url': video_url, 'audio_url': audio_url}
    except Exception as e:
        print(f"解析视频错误: {e}")
        return None


# ========== Celery 任务 ==========
@app.task(bind=True, autoretry_for=(ChunkedEncodingError, ConnectionError, Timeout, Exception), retry_backoff=2, retry_kwargs={'max_retries': 3}, retry_jitter=True)
def download_video_task(self, url):
    """异步下载视频任务"""
    task_id = self.request.id
    retry_count = self.request.retries
    print(f"开始处理任务: {task_id} (尝试 {retry_count + 1}/4)")

    try:
        info = parse_video_page(url)
        if not info:
            set_task_status(task_id, {'status': 'failed', 'error': '解析视频失败'})
            return {'status': 'failed', 'error': '解析视频失败'}

        # 下载视频
        video_content = None
        for attempt in range(3):
            try:
                video_resp = requests.get(info['video_url'], headers=BILI_HEADERS, cookies=BILI_COOKIES, timeout=120, stream=True)
                video_content = b''
                for chunk in video_resp.iter_content(chunk_size=8192):
                    if chunk:
                        video_content += chunk
                break
            except Exception as e:
                print(f"视频下载失败 (尝试 {attempt+1}/3): {e}")
                if attempt == 2:
                    raise
                time.sleep(2)

        if video_content is None:
            raise Exception("视频下载失败")

        # 下载音频
        audio_content = None
        for attempt in range(3):
            try:
                audio_resp = requests.get(info['audio_url'], headers=BILI_HEADERS, cookies=BILI_COOKIES, timeout=120, stream=True)
                audio_content = b''
                for chunk in audio_resp.iter_content(chunk_size=8192):
                    if chunk:
                        audio_content += chunk
                break
            except Exception as e:
                print(f"音频下载失败 (尝试 {attempt+1}/3): {e}")
                if attempt == 2:
                    raise
                time.sleep(2)

        if audio_content is None:
            raise Exception("音频下载失败")

        # 保存临时文件
        temp_id = str(uuid.uuid4())[:8]
        video_path = f'/tmp/{temp_id}_{info["title"]}.mp4'
        audio_path = f'/tmp/{temp_id}_{info["title"]}.mp3'
        output_path = f'/tmp/{temp_id}_{info["title"]}_合成.mp4'

        with open(video_path, 'wb') as f:
            f.write(video_content)
        with open(audio_path, 'wb') as f:
            f.write(audio_content)

        # 合成视频
        ffmpeg_available = False
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5, check=False)
            ffmpeg_available = True
        except:
            pass

        if ffmpeg_available:
            cmd = f'ffmpeg -i "{video_path}" -i "{audio_path}" -c:v copy -c:a copy "{output_path}" -y -loglevel error'
            subprocess.run(cmd, shell=True, timeout=600, check=True)
            for path in [video_path, audio_path]:
                if os.path.exists(path):
                    os.remove(path)

        if os.path.exists(output_path):
            set_task_status(task_id, {'status': 'completed', 'progress': 100})
            print(f"合成完成: {output_path}")
            # 存储到 Redis
            store_video_to_redis(task_id, output_path)
            return {'status': 'completed', 'file_path': output_path, 'title': info['title'], 'type': 'full'}
        else:
            return {'status': 'failed', 'error': '合成失败'}

    except (ChunkedEncodingError, ConnectionError, Timeout) as e:
        print(f"下载中断 (尝试 {retry_count + 1}/4): {e}")
        set_task_status(task_id, {'status': 'retrying', 'retry': retry_count + 1})
        raise self.retry(exc=e, countdown=2 ** (retry_count + 1))
    except Exception as e:
        print(f"任务失败: {e}")
        import traceback
        traceback.print_exc()
        set_task_status(task_id, {'status': 'failed', 'error': str(e)})
        return {'status': 'failed', 'error': str(e)}
EOF
