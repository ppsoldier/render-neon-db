from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
import os

app = Flask(__name__)
CORS(app)

# 数据库连接
def get_db_connection():
    conn = psycopg2.connect(
        host=os.environ.get('DB_HOST'),
        user=os.environ.get('DB_USER'),
        password=os.environ.get('DB_PASSWORD'),
        database=os.environ.get('DB_NAME'),
        port=5432,
        sslmode='require'
    )
    return conn

# 1. 测试接口
@app.route('/')
def home():
    return "✅ 后端服务运行成功！"

# 2. 查询腾讯招聘岗位列表
@app.route('/api/jobs', methods=['GET'])
def get_jobs():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # 查询所有岗位数据
        cur.execute("SELECT id, work_name, work_site, work_year, work_require FROM tenxun_zhaoping;")
        jobs = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({
            "code": 200,
            "msg": "获取岗位列表成功",
            "data": jobs
        })
    except Exception as e:
        return jsonify({"code": 500, "msg": "获取失败", "error": str(e)})

# 3. 新增岗位（可选）
@app.route('/api/jobs/add', methods=['POST'])
def add_job():
    data = request.json
    work_name = data.get('work_name')
    work_site = data.get('work_site')
    work_year = data.get('work_year')
    work_require = data.get('work_require')

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO tenxun_zhaoping (work_name, work_site, work_year, work_require) VALUES (%s, %s, %s, %s)",
        (work_name, work_site, work_year, work_require)
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"code": 200, "msg": "岗位新增成功"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
