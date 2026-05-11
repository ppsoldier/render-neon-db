from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
import os
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

def get_db():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        port=5432,
        sslmode="require"
    )

# ----------------------- 登录 -----------------------
@app.route("/api/login", methods=["POST"])
def login():
    d = request.json
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT id, name, role FROM "user"
        WHERE phone=%s AND password=%s
    """, (d['phone'], d['password']))
    user = cur.fetchone()
    cur.close()
    db.close()
    if user:
        return jsonify({"code":200,"data":{"id":user[0],"name":user[1],"role":user[2]}})
    return jsonify({"code":403,"msg":"账号或密码错误"})

# ----------------------- 学生管理 -----------------------
@app.route("/api/student/list")
def student_list():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id,name,phone,grade,school FROM student")
    data = cur.fetchall()
    cur.close()
    db.close()
    return jsonify({"code":200,"data":data})

@app.route("/api/student/add", methods=["POST"])
def student_add():
    d = request.json
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO student (name,phone,grade,school)
        VALUES (%s,%s,%s,%s)
    """, (d['name'],d['phone'],d['grade'],d['school']))
    db.commit()
    cur.close()
    db.close()
    return jsonify({"code":200,"msg":"添加成功"})

@app.route("/api/student/update", methods=["POST"])
def student_update():
    d = request.json
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        UPDATE student SET name=%s,phone=%s,grade=%s,school=%s
        WHERE id=%s
    """, (d['name'],d['phone'],d['grade'],d['school'],d['id']))
    db.commit()
    cur.close()
    db.close()
    return jsonify({"code":200,"msg":"修改成功"})

@app.route("/api/student/delete", methods=["POST"])
def student_delete():
    id = request.json.get('id')
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM student WHERE id=%s", (id,))
    db.commit()
    cur.close()
    db.close()
    return jsonify({"code":200,"msg":"删除成功"})

# ----------------------- 教师管理 -----------------------
@app.route("/api/teacher/list")
def teacher_list():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id,name,phone,subject,class_fee FROM teacher")
    data = cur.fetchall()
    cur.close()
    db.close()
    return jsonify({"code":200,"data":data})

@app.route("/api/teacher/add", methods=["POST"])
def teacher_add():
    d = request.json
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO teacher (name,phone,subject,class_fee)
        VALUES (%s,%s,%s,%s)
    """, (d['name'],d['phone'],d['subject'],d['class_fee']))
    db.commit()
    cur.close()
    db.close()
    return jsonify({"code":200,"msg":"添加成功"})

# ----------------------- 课时管理 -----------------------
@app.route("/api/course/list")
def course_list():
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT cp.id, s.name, cp.total, cp.used, cp.surplus
        FROM course_package cp
        JOIN student s ON cp.student_id = s.id
    """)
    data = cur.fetchall()
    cur.close()
    db.close()
    return jsonify({"code":200,"data":data})

@app.route("/api/course/add", methods=["POST"])
def course_add():
    d = request.json
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO course_package (student_id,total,used,surplus)
        VALUES (%s,%s,0,%s)
    """, (d['student_id'], d['total'], d['total']))
    db.commit()
    cur.close()
    db.close()
    return jsonify({"code":200,"msg":"设置成功"})

# ----------------------- 排课管理 -----------------------
@app.route("/api/schedule/list")
def schedule_list():
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT cs.id, s.name student, t.name teacher,
        cs.subject, cs.class_date, cs.class_time, cs.classroom
        FROM course_schedule cs
        JOIN student s ON cs.student_id = s.id
        JOIN teacher t ON cs.teacher_id = t.id
        ORDER BY cs.class_date DESC
    """)
    data = cur.fetchall()
    cur.close()
    db.close()
    return jsonify({"code":200,"data":data})

@app.route("/api/schedule/add", methods=["POST"])
def schedule_add():
    d = request.json
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO course_schedule
        (student_id,teacher_id,subject,class_date,class_time,classroom)
        VALUES (%s,%s,%s,%s,%s,%s)
    """, (d['sid'],d['tid'],d['subject'],d['date'],d['time'],d['room']))
    db.commit()
    cur.close()
    db.close()
    return jsonify({"code":200,"msg":"排课成功"})

# ----------------------- 首页 -----------------------
@app.route("/")
def index():
    return "✅ 培训机构管理系统 运行成功"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
