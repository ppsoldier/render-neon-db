from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
import os

app = Flask(__name__)
CORS(app)

# 从 Render 环境变量读取，不要硬编码密码！
config = {
    'host': os.environ.get('DB_HOST'),
    'user': os.environ.get('DB_USER'),
    'password': os.environ.get('DB_PASSWORD'),
    'database': os.environ.get('DB_NAME'),
    'port': 5432,
    'sslmode': 'require'  # Neon 必须
}

@app.route('/api/query', methods=['GET'])
def query():
    try:
        conn = psycopg2.connect(**config)
        cur = conn.cursor()
        # 先测试一条简单 SQL，能跑通再改你的表
        cur.execute("SELECT version();")
        data = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"code":200, "data":data})
    except Exception as e:
        return jsonify({"code":500, "msg":str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))