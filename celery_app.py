# celery_app.py
from celery import Celery
import os

# 从环境变量读取 Redis 地址（支持 Railway 自动注入）
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
print(f"使用 Redis 地址: {REDIS_URL}")

app = Celery(
    'video_tasks',
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=['tasks']
)

app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Asia/Shanghai',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,       # 1小时超时
    task_soft_time_limit=3000,   # 50分钟软超时
    worker_prefetch_multiplier=1,
    result_expires=3600,         # 结果过期时间
)

if __name__ == '__main__':
    app.start()
