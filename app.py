from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
import os

app = Flask(__name__)
CORS(app)

# 数据库连接配置
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

# 1. 首页测试
@app.route('/')
def home():
    return "✅ 后端服务运行成功！"

# 2. 获取所有学生列表
@app.route('/api/students', methods=['GET'])
def get_students():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # 查询 students 表的所有数据
        cur.execute("SELECT id, student_id, name, gender FROM students;")
        students = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({
            "code": 200,
            "msg": "获取学生列表成功",
            "data": students
        })
    except Exception as e:
        return jsonify({"code": 500, "msg": "获取失败", "error": str(e)})

# 3. 新增学生（可选）
@app.route('/api/students/add', methods=['POST'])
def add_student():
    data = request.json
    student_id = data.get('student_id')
    name = data.get('name')
    gender = data.get('gender')

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO students (student_id, name, gender) VALUES (%s, %s, %s)",
        (student_id, name, gender)
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"code": 200, "msg": "学生新增成功"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
