# celery_app.py
from celery import Celery
import os

# Redis 配置（Railway 提供 Redis 服务）
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
app = Celery(
    'video_tasks',
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=['tasks']  # 包含任务模块
)

app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Asia/Shanghai',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1小时超时
    task_soft_time_limit=3000,  # 50分钟软超时
    worker_prefetch_multiplier=1,
)
