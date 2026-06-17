FROM python:3.13-slim

# 安装 ffmpeg（每次构建都会执行）
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .

CMD ["celery", "-A", "celery_app", "worker", "--loglevel=info", "--concurrency=1"]
