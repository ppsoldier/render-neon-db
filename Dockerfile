FROM python:3.13-slim

# 注释掉 ffmpeg 安装
# RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .

CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8080", "--timeout", "120"]
