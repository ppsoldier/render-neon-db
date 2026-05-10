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

# ===================== 接口列表 =====================

# 1. 测试接口
@app.route('/')
def home():
    return "✅ 后端服务运行成功！"

# 2. 测试数据库连接
@app.route('/api/query')
def test_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT version();")
        version = cur.fetchone()
        cur.close()
        conn.close()
        return jsonify({
            "code": 200,
            "msg": "数据库连接成功",
            "data": version
        })
    except Exception as e:
        return jsonify({"code":500,"msg":"数据库错误","error":str(e)})

# 3. 创建表（只需调用一次）
@app.route('/api/create_table')
def create_table():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                name VARCHAR(50) NOT NULL,
                age INT,
                phone VARCHAR(20)
            );
        ''')
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"code":200,"msg":"表创建成功"})
    except Exception as e:
        return jsonify({"code":500,"msg":"创建失败","error":str(e)})

# 4. 添加用户
@app.route('/api/add', methods=['POST'])
def add_user():
    data = request.json
    name = data.get('name')
    age = data.get('age')
    phone = data.get('phone')

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (name, age, phone) VALUES (%s, %s, %s)",
        (name, age, phone)
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"code":200,"msg":"添加成功"})

# 5. 查询所有用户
@app.route('/api/list')
def get_list():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users;")
    users = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify({"code":200,"data":users})

# 6. 修改用户
@app.route('/api/update', methods=['POST'])
def update_user():
    data = request.json
    id = data.get('id')
    name = data.get('name')
    age = data.get('age')
    phone = data.get('phone')

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET name=%s, age=%s, phone=%s WHERE id=%s",
        (name, age, phone, id)
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"code":200,"msg":"修改成功"})

# 7. 删除用户
@app.route('/api/delete', methods=['POST'])
def delete_user():
    data = request.json
    id = data.get('id')

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE id=%s", (id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"code":200,"msg":"删除成功"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
