from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
import os

app = Flask(__name__)
CORS(app)

# 数据库连接
def get_db_connection():
    return psycopg2.connect(
        host=os.environ.get('DB_HOST'),
        user=os.environ.get('DB_USER'),
        password=os.environ.get('DB_PASSWORD'),
        database=os.environ.get('DB_NAME'),
        port=5432,
        sslmode='require'
    )

# ------------------- 学生表 增删改查 -------------------

# 1. 查询所有学生
@app.route('/api/students', methods=['GET'])
def get_students():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, student_id, name, gender FROM students ORDER BY id;")
        students = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"code":200,"data":students})
    except:
        return jsonify({"code":500,"msg":"服务器错误"})

# 2. 新增学生
@app.route('/api/students/add', methods=['POST'])
def add_student():
    d = request.json
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO students (student_id, name, gender) VALUES (%s,%s,%s)",
        (d['student_id'], d['name'], d['gender'])
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"code":200,"msg":"添加成功"})

# 3. 修改学生
@app.route('/api/students/update', methods=['POST'])
def update_student():
    d = request.json
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE students SET student_id=%s, name=%s, gender=%s WHERE id=%s",
        (d['student_id'], d['name'], d['gender'], d['id'])
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"code":200,"msg":"修改成功"})

# 4. 删除学生
@app.route('/api/students/delete', methods=['POST'])
def delete_student():
    id = request.json.get('id')
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM students WHERE id=%s", (id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"code":200,"msg":"删除成功"})

# 主页
@app.route('/')
def home():
    return "✅ 学生管理系统运行成功"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
