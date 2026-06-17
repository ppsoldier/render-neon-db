# tasks.py
from celery_app import app
import requests
import re
import os
import uuid
import subprocess
import time

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

BILI_HEADERS = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'cache-control': 'no-cache',
            'pragma': 'no-cache',
            'priority': 'u=0, i',
            'referer': 'https://search.bilibili.com/all?keyword=%E8%88%9E%E8%B9%88+%E8%87%AD%E5%BC%9F%E5%BC%9F&from_source=webtop_search&spm_id_from=333.1007&search_source=5',
            'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Microsoft Edge";v="144"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-site',
            'sec-fetch-user': '?1',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0'
            }

# 任务状态存储（可改用 Redis 持久化）
task_status = {}
task_results = {}

@app.task(bind=True)
def download_video_task(self, url):
    """异步下载和合成视频"""
    task_id = self.request.id
    task_status[task_id] = {'status': 'started', 'progress': 0}
    
    try:
        # 1. 解析视频信息
        task_status[task_id] = {'status': 'parsing', 'progress': 10}
        info = parse_video_page(url)
        if not info:
            task_status[task_id] = {'status': 'failed', 'error': '解析视频失败'}
            return {'status': 'failed', 'error': '解析视频失败'}
        
        # 2. 下载视频
        task_status[task_id] = {'status': 'downloading_video', 'progress': 20}
        video_content = download_with_progress(info['video_url'], task_id, 'video')
        
        # 3. 下载音频
        task_status[task_id] = {'status': 'downloading_audio', 'progress': 40}
        audio_content = download_with_progress(info['audio_url'], task_id, 'audio')
        
        # 4. 保存临时文件
        task_status[task_id] = {'status': 'saving', 'progress': 60}
        temp_id = str(uuid.uuid4())[:8]
        video_path = f'/tmp/{temp_id}_{info["title"]}.mp4'
        audio_path = f'/tmp/{temp_id}_{info["title"]}.mp3'
        output_path = f'/tmp/{temp_id}_{info["title"]}_合成.mp4'
        
        with open(video_path, 'wb') as f:
            f.write(video_content)
        with open(audio_path, 'wb') as f:
            f.write(audio_content)
        
        # 5. 合成视频
        task_status[task_id] = {'status': 'merging', 'progress': 70}
        cmd = f'ffmpeg -i "{video_path}" -i "{audio_path}" -c:v copy -c:a copy "{output_path}" -y -loglevel error'
        result = subprocess.run(cmd, shell=True, timeout=600, capture_output=True)
        
        if result.returncode != 0 or not os.path.exists(output_path):
            # 合成失败，返回纯视频文件
            task_status[task_id] = {'status': 'completed', 'progress': 100, 'result': 'video_only'}
            return {'status': 'completed', 'file_path': video_path, 'title': info['title'], 'type': 'video_only'}
        
        # 6. 清理临时文件
        os.remove(video_path)
        os.remove(audio_path)
        
        task_status[task_id] = {'status': 'completed', 'progress': 100}
        return {'status': 'completed', 'file_path': output_path, 'title': info['title'], 'type': 'full'}
        
    except Exception as e:
        task_status[task_id] = {'status': 'failed', 'error': str(e)}
        return {'status': 'failed', 'error': str(e)}

def parse_video_page(url):
    """解析视频页面（同 app.py）"""
    resp = requests.get(url, cookies=BILI_COOKIES, headers=BILI_HEADERS, timeout=10)
    html = resp.text
    title_match = re.search(r'<title>(.*?)_哔哩哔哩_bilibili</title>', html)
    if not title_match:
        return None
    title = title_match.group(1).replace(' ', '').replace('|', '').replace("'", '').replace('/', '_')
    video_match = re.search(r'"video".*?"baseUrl":"(.*?)"', html)
    audio_match = re.search(r'"audio".*?"baseUrl":"(.*?)"', html)
    if not video_match or not audio_match:
        return None
    return {
        'title': title,
        'video_url': video_match.group(1),
        'audio_url': audio_match.group(1)
    }

def download_with_progress(url, task_id, media_type):
    """带进度的下载"""
    resp = requests.get(url, headers=BILI_HEADERS, stream=True, timeout=60)
    total_size = int(resp.headers.get('content-length', 0))
    content = bytearray()
    downloaded = 0
    
    for chunk in resp.iter_content(chunk_size=8192):
        if chunk:
            content.extend(chunk)
            downloaded += len(chunk)
            if total_size > 0:
                progress = 40 + int((downloaded / total_size) * 20)
                if media_type == 'audio':
                    progress = 40 + int((downloaded / total_size) * 20)
                task_status[task_id]['progress'] = progress
    
    return bytes(content)

# 获取任务状态
def get_task_status(task_id):
    return task_status.get(task_id, {'status': 'not_found'})

def get_task_result(task_id):
    return task_results.get(task_id)
