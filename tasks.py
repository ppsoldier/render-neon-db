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
from datetime import datetime
from requests.exceptions import ChunkedEncodingError, ConnectionError, Timeout

# ========== 任务状态存储 ==========
_task_status_store = {}
_task_result_store = {}

def set_task_status(task_id, status):
    """设置任务状态"""
    _task_status_store[task_id] = status

def get_task_status(task_id):
    """获取任务状态"""
    return _task_status_store.get(task_id, {'status': 'not_found'})

def set_task_result(task_id, result):
    """设置任务结果"""
    _task_result_store[task_id] = result

def get_task_result(task_id):
    """获取任务结果"""
    return _task_result_store.get(task_id)


# ========== B站请求配置 ==========
BILI_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://www.bilibili.com/',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

# B站Cookie（从浏览器获取，过期需更新）
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


# ========== 辅助函数 ==========
def parse_video_page(url):
    """使用 B 站 API 获取视频信息"""
    bv_match = re.search(r'BV([a-zA-Z0-9]+)', url)
    if not bv_match:
        print(f"未找到 BV 号: {url}")
        return None

    bvid = bv_match.group(0)
    print(f"解析 BV 号: {bvid}")

    try:
        api_url = f'https://api.bilibili.com/x/web-interface/view?bvid={bvid}'
        resp = requests.get(api_url, headers=BILI_HEADERS, cookies=BILI_COOKIES, timeout=10)
        data = resp.json()
        print(f"B站API返回: code={data.get('code')}")

        if data.get('code') != 0:
            print(f"B站API错误: {data.get('message')}")
            return None

        video_data = data['data']
        title = video_data.get('title', '未知标题')
        cid = video_data.get('cid')
        print(f"视频标题: {title}, CID: {cid}")

        # 获取播放地址
        play_url = f'https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&qn=80&fnval=16&fourk=1'
        play_resp = requests.get(play_url, headers=BILI_HEADERS, cookies=BILI_COOKIES, timeout=10)
        play_data = play_resp.json()

        if play_data.get('code') != 0:
            print(f"获取播放地址失败: {play_data.get('message')}")
            return None

        dash = play_data['data'].get('dash', {})
        video_url = dash['video'][0].get('baseUrl') if dash.get('video') else None
        audio_url = dash['audio'][0].get('baseUrl') if dash.get('audio') else None

        if not video_url:
            print("未找到视频地址")
            return None

        title = title.replace(' ', '').replace('|', '').replace("'", '').replace('/', '_')

        return {
            'title': title,
            'video_url': video_url,
            'audio_url': audio_url
        }

    except Exception as e:
        print(f"解析视频错误: {e}")
        return None


# ========== Celery 任务（带自动重试）==========
@app.task(
    bind=True,
    autoretry_for=(ChunkedEncodingError, ConnectionError, Timeout, Exception),
    retry_backoff=2,
    retry_kwargs={'max_retries': 3},
    retry_jitter=True
)
def download_video_task(self, url):
    """异步下载视频任务（带自动重试）"""
    task_id = self.request.id
    retry_count = self.request.retries
    print(f"开始处理任务: {task_id} (尝试 {retry_count + 1}/4), URL: {url}")

    try:
        # 1. 解析视频信息
        info = parse_video_page(url)
        if not info:
            set_task_status(task_id, {'status': 'failed', 'error': '解析视频失败'})
            return {'status': 'failed', 'error': '解析视频失败'}

        print(f"解析成功: {info['title']}")

        # 2. 下载视频（带重试）
        video_content = None
        for attempt in range(3):
            try:
                print(f"开始下载视频 (尝试 {attempt+1}/3)...")
                video_resp = requests.get(info['video_url'], headers=BILI_HEADERS, cookies=BILI_COOKIES, timeout=120, stream=True)
                video_content = b''
                downloaded = 0
                total_size = int(video_resp.headers.get('content-length', 0))
                for chunk in video_resp.iter_content(chunk_size=8192):
                    if chunk:
                        video_content += chunk
                        downloaded += len(chunk)
                        if total_size > 0:
                            progress = min(50, int(downloaded / total_size * 50))
                            self.update_state(state='PROGRESS', meta={'progress': 20 + progress, 'status': 'downloading_video'})
                print(f"视频下载完成: {len(video_content)} bytes")
                break
            except (ChunkedEncodingError, ConnectionError, Timeout) as e:
                print(f"视频下载失败 (尝试 {attempt+1}/3): {e}")
                if attempt == 2:
                    raise
                time.sleep(2)

        if video_content is None:
            raise Exception("视频下载失败")

        # 3. 下载音频
        print("开始下载音频...")
        audio_content = None
        for attempt in range(3):
            try:
                audio_resp = requests.get(info['audio_url'], headers=BILI_HEADERS, cookies=BILI_COOKIES, timeout=120, stream=True)
                audio_content = b''
                downloaded = 0
                total_size = int(audio_resp.headers.get('content-length', 0))
                for chunk in audio_resp.iter_content(chunk_size=8192):
                    if chunk:
                        audio_content += chunk
                        downloaded += len(chunk)
                        if total_size > 0:
                            progress = min(70, 50 + int(downloaded / total_size * 20))
                            self.update_state(state='PROGRESS', meta={'progress': progress, 'status': 'downloading_audio'})
                print(f"音频下载完成: {len(audio_content)} bytes")
                break
            except (ChunkedEncodingError, ConnectionError, Timeout) as e:
                print(f"音频下载失败 (尝试 {attempt+1}/3): {e}")
                if attempt == 2:
                    raise
                time.sleep(2)

        if audio_content is None:
            raise Exception("音频下载失败")

        # 4. 保存临时文件
        temp_id = str(uuid.uuid4())[:8]
        video_path = f'/tmp/{temp_id}_{info["title"]}.mp4'
        audio_path = f'/tmp/{temp_id}_{info["title"]}.mp3'
        output_path = f'/tmp/{temp_id}_{info["title"]}_合成.mp4'

        with open(video_path, 'wb') as f:
            f.write(video_content)
        with open(audio_path, 'wb') as f:
            f.write(audio_content)

        self.update_state(state='PROGRESS', meta={'progress': 80, 'status': 'merging'})

        # 5. 检查 ffmpeg 是否可用
        ffmpeg_available = False
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5, check=False)
            ffmpeg_available = True
        except:
            pass

        if ffmpeg_available:
            print("开始合成视频...")
            cmd = f'ffmpeg -i "{video_path}" -i "{audio_path}" -c:v copy -c:a copy "{output_path}" -y -loglevel error'
            try:
                subprocess.run(cmd, shell=True, timeout=600, check=True)
                # 清理临时文件
                for path in [video_path, audio_path]:
                    if os.path.exists(path):
                        os.remove(path)
                        print(f"已删除临时文件: {path}")
                if os.path.exists(output_path):
                    set_task_status(task_id, {'status': 'completed', 'progress': 100})
                    print(f"合成完成: {output_path}")
                    return {'status': 'completed', 'file_path': output_path, 'title': info['title'], 'type': 'full'}
                else:
                    return {'status': 'failed', 'error': '合成失败'}
            except subprocess.TimeoutExpired:
                print("合成超时，返回纯视频文件")
                return {'status': 'completed', 'file_path': video_path, 'title': info['title'], 'type': 'video_only'}
        else:
            print("ffmpeg 不可用，返回纯视频文件")
            return {'status': 'completed', 'file_path': video_path, 'title': info['title'], 'type': 'video_only'}

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


@app.task
def search_videos_task(keyword, page=1, page_size=20):
    """搜索视频任务"""
    try:
        search_url = 'https://api.bilibili.com/x/web-interface/search/type'
        params = {
            'search_type': 'video',
            'keyword': keyword,
            'page': page,
            'pagesize': page_size,
        }
        resp = requests.get(search_url, headers=BILI_HEADERS, cookies=BILI_COOKIES, timeout=10)
        data = resp.json()
        if data.get('code') != 0:
            return {'status': 'failed', 'error': data.get('message')}
        results = data.get('data', {}).get('result', [])
        video_list = []
        for item in results:
            video_list.append({
                'bvid': item.get('bvid'),
                'title': item.get('title', '').replace('<em class="keyword">', '').replace('</em>', ''),
                'author': item.get('author', ''),
                'pic': item.get('pic', ''),
                'duration': item.get('duration', 0),
                'play': item.get('play', 0),
                'danmaku': item.get('danmaku', 0),
                'url': f"https://www.bilibili.com/video/{item.get('bvid')}"
            })
        return {'status': 'completed', 'data': video_list, 'total': len(video_list)}
    except Exception as e:
        return {'status': 'failed', 'error': str(e)}
