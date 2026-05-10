from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
import os

app = Flask(__name__)
CORS(app)

# 数据库配置
config = {
    'host': os.environ.get('DB_HOST'),
    'user': os.environ.get('DB_USER'),
    'password': os.environ.get('DB_PASSWORD'),
    'database': os.environ.get('DB_NAME'),
    'port': 5432,
    'sslmode': 'require'
}

# 根路由（测试服务）
@app.route('/')
def index():
    return "✅ 服务启动成功！终于搞定啦！"

# 数据库查询接口
@app.route('/api/query', methods=['GET'])
def query():
    try:
        conn = psycopg2.connect(**config)
        cur = conn.cursor()
        cur.execute("SELECT version();")
        data = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({
            "code": 200,
            "message": "数据库连接成功",
            "data": data
        })
    except Exception as e:
        return jsonify({
            "code": 500,
            "message": "数据库连接失败",
            "error": str(e)
        })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
