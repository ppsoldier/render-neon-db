from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import pg8000
import os
from datetime import datetime, timedelta
import io
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill

app = Flask(__name__)
CORS(app)

def get_db():
    """获取数据库连接 - 使用 pg8000 纯 Python 驱动"""
    return pg8000.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        port=5432,
        ssl=True  # Neon 需要 SSL
    )

# ------------------- 健康检查 -------------------
@app.route("/")
def index():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})

@app.route("/health")
def health():
    return jsonify({"status": "healthy"})

# ==================== 用户管理模块 ====================
@app.route("/api/login", methods=["POST"])
def login():
    """用户登录"""
    d = request.json
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id, name, role FROM \"user\" WHERE phone=%s AND password=%s",
                (d.get('phone'), d.get('password')))
    user = cur.fetchone()
    cur.close()
    db.close()
    if user:
        return jsonify({"code": 200, "data": {"id": user[0], "name": user[1], "role": user[2]}})
    return jsonify({"code": 403, "msg": "账号或密码错误"})

@app.route("/api/user/list")
def user_list():
    """获取用户列表"""
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id, phone, name, role, status, created_at FROM \"user\" ORDER BY id")
    data = cur.fetchall()
    cur.close()
    db.close()
    result = [{"id": r[0], "phone": r[1], "name": r[2], "role": r[3], 
               "status": r[4], "created_at": str(r[5]) if r[5] else None} for r in data]
    return jsonify({"code": 200, "data": result})

@app.route("/api/user/add", methods=["POST"])
def user_add():
    """添加用户"""
    d = request.json
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("INSERT INTO \"user\" (phone, password, name, role, status) VALUES (%s, %s, %s, %s, %s)",
                    (d['phone'], d['password'], d['name'], d['role'], d.get('status', 1)))
        db.commit()
        return jsonify({"code": 200, "msg": "添加成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"code": 500, "msg": f"添加失败: {str(e)}"})
    finally:
        cur.close()
        db.close()

@app.route("/api/user/update", methods=["POST"])
def user_update():
    """更新用户信息"""
    d = request.json
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("UPDATE \"user\" SET phone=%s, name=%s, role=%s, status=%s WHERE id=%s",
                    (d['phone'], d['name'], d['role'], d.get('status', 1), d['id']))
        db.commit()
        return jsonify({"code": 200, "msg": "更新成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"code": 500, "msg": f"更新失败: {str(e)}"})
    finally:
        cur.close()
        db.close()

@app.route("/api/user/delete", methods=["POST"])
def user_delete():
    """删除用户"""
    id = request.json.get('id')
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("DELETE FROM \"user\" WHERE id=%s", (id,))
        db.commit()
        return jsonify({"code": 200, "msg": "删除成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"code": 500, "msg": f"删除失败: {str(e)}"})
    finally:
        cur.close()
        db.close()

# ==================== 学生管理模块 ====================
@app.route("/api/student/list")
def student_list():
    """获取学生列表"""
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id, name, phone, grade, school FROM student")
    data = cur.fetchall()
    cur.close()
    db.close()
    result = [{"id": r[0], "name": r[1], "phone": r[2], "grade": r[3], "school": r[4]} for r in data]
    return jsonify({"code": 200, "data": result})

@app.route("/api/student/add", methods=["POST"])
def student_add():
    """添加学生"""
    d = request.json
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("INSERT INTO student (name, phone, grade, school) VALUES (%s, %s, %s, %s)",
                    (d['name'], d['phone'], d['grade'], d['school']))
        db.commit()
        return jsonify({"code": 200, "msg": "添加成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"code": 500, "msg": f"添加失败: {str(e)}"})
    finally:
        cur.close()
        db.close()

@app.route("/api/student/update", methods=["POST"])
def student_update():
    """更新学生信息"""
    d = request.json
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("UPDATE student SET name=%s, phone=%s, grade=%s, school=%s WHERE id=%s",
                    (d['name'], d['phone'], d['grade'], d['school'], d['id']))
        db.commit()
        return jsonify({"code": 200, "msg": "更新成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"code": 500, "msg": f"更新失败: {str(e)}"})
    finally:
        cur.close()
        db.close()

@app.route("/api/student/delete", methods=["POST"])
def student_delete():
    """删除学生"""
    id = request.json.get('id')
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("DELETE FROM student WHERE id=%s", (id,))
        db.commit()
        return jsonify({"code": 200, "msg": "删除成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"code": 500, "msg": f"删除失败: {str(e)}"})
    finally:
        cur.close()
        db.close()

# ==================== 教师管理模块 ====================
@app.route("/api/teacher/list")
def teacher_list():
    """获取教师列表"""
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id, name, phone, subject, class_fee FROM teacher")
    data = cur.fetchall()
    cur.close()
    db.close()
    result = [{"id": r[0], "name": r[1], "phone": r[2], "subject": r[3], "class_fee": float(r[4]) if r[4] else 0} for r in data]
    return jsonify({"code": 200, "data": result})

@app.route("/api/teacher/add", methods=["POST"])
def teacher_add():
    """添加教师"""
    d = request.json
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("INSERT INTO teacher (name, phone, subject, class_fee) VALUES (%s, %s, %s, %s)",
                    (d['name'], d['phone'], d['subject'], d['class_fee']))
        db.commit()
        return jsonify({"code": 200, "msg": "添加成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"code": 500, "msg": f"添加失败: {str(e)}"})
    finally:
        cur.close()
        db.close()

@app.route("/api/teacher/update", methods=["POST"])
def teacher_update():
    """更新教师信息"""
    d = request.json
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("UPDATE teacher SET name=%s, phone=%s, subject=%s, class_fee=%s WHERE id=%s",
                    (d['name'], d['phone'], d['subject'], d['class_fee'], d['id']))
        db.commit()
        return jsonify({"code": 200, "msg": "更新成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"code": 500, "msg": f"更新失败: {str(e)}"})
    finally:
        cur.close()
        db.close()

@app.route("/api/teacher/delete", methods=["POST"])
def teacher_delete():
    """删除教师"""
    id = request.json.get('id')
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("DELETE FROM teacher WHERE id=%s", (id,))
        db.commit()
        return jsonify({"code": 200, "msg": "删除成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"code": 500, "msg": f"删除失败: {str(e)}"})
    finally:
        cur.close()
        db.close()

# ==================== 课时管理模块 ====================
@app.route("/api/course/list")
def course_list():
    """获取课时包列表"""
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT cp.id, s.name, cp.total, cp.used, cp.surplus FROM course_package cp JOIN student s ON cp.student_id=s.id")
    data = cur.fetchall()
    cur.close()
    db.close()
    result = [{"id": r[0], "student_name": r[1], "total": float(r[2]) if r[2] else 0,
               "used": float(r[3]) if r[3] else 0, "surplus": float(r[4]) if r[4] else 0} for r in data]
    return jsonify({"code": 200, "data": result})

@app.route("/api/course/add", methods=["POST"])
def course_add():
    """添加课时包"""
    d = request.json
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("INSERT INTO course_package (student_id, total, used, surplus) VALUES (%s, %s, 0, %s)",
                    (d['student_id'], d['total'], d['total']))
        db.commit()
        return jsonify({"code": 200, "msg": "添加成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"code": 500, "msg": f"添加失败: {str(e)}"})
    finally:
        cur.close()
        db.close()

# ==================== 排课管理模块 ====================
@app.route("/api/schedule/calendar")
def schedule_calendar():
    """获取日历排课数据"""
    date = request.args.get('date')
    db = get_db()
    cur = db.cursor()
    cur.execute('''SELECT cs.id, s.name, t.name, cs.subject, cs.class_time, cs.classroom
                   FROM course_schedule cs
                   JOIN student s ON cs.student_id=s.id
                   JOIN teacher t ON cs.teacher_id=t.id
                   WHERE cs.class_date=%s''', (date,))
    data = cur.fetchall()
    cur.close()
    db.close()
    result = [{"id": r[0], "student_name": r[1], "teacher_name": r[2], "subject": r[3], "class_time": r[4], "classroom": r[5]} for r in data]
    return jsonify({"code": 200, "data": result})

@app.route("/api/schedule/check", methods=["POST"])
def schedule_check():
    """检查排课冲突"""
    d = request.json
    db = get_db()
    cur = db.cursor()
    cur.execute('''SELECT COUNT(*) FROM course_schedule
                   WHERE (teacher_id=%s OR classroom=%s)
                   AND class_date=%s AND class_time=%s AND id!=%s''',
                (d.get('tid'), d.get('room'), d['date'], d['time'], d.get('id', 0)))
    cnt = cur.fetchone()[0]
    cur.close()
    db.close()
    return jsonify({"code": 200, "conflict": cnt > 0})

@app.route("/api/schedule/save", methods=["POST"])
def schedule_save():
    """保存排课"""
    d = request.json
    db = get_db()
    cur = db.cursor()
    days = []
    if int(d.get('repeat', 0)) == 1:
        base = datetime.strptime(d['date'], "%Y-%m-%d")
        for i in range(8):
            days.append((base + timedelta(days=i*7)).strftime("%Y-%m-%d"))
    else:
        days.append(d['date'])
    try:
        for day in days:
            wd = datetime.strptime(day, "%Y-%m-%d").weekday() + 1
            cur.execute('''INSERT INTO course_schedule
                           (student_id, teacher_id, subject, class_date, class_time, classroom, week_day, repeat_type)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''',
                        (d.get('sid'), d.get('tid'), d['subject'], day, d['time'], d['room'], wd, d.get('repeat', 0)))
        db.commit()
        return jsonify({"code": 200, "msg": f"成功添加{len(days)}节课"})
    except Exception as e:
        db.rollback()
        return jsonify({"code": 500, "msg": f"保存失败: {str(e)}"})
    finally:
        cur.close()
        db.close()

@app.route("/api/schedule/update", methods=["POST"])
def schedule_update():
    """更新排课"""
    d = request.json
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute('''UPDATE course_schedule 
                       SET student_id=%s, teacher_id=%s, subject=%s, class_date=%s, class_time=%s, classroom=%s 
                       WHERE id=%s''',
                    (d.get('sid'), d.get('tid'), d['subject'], d['date'], d['time'], d['room'], d['id']))
        db.commit()
        return jsonify({"code": 200, "msg": "更新成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"code": 500, "msg": f"更新失败: {str(e)}"})
    finally:
        cur.close()
        db.close()

@app.route("/api/schedule/delete", methods=["POST"])
def schedule_delete():
    """删除排课"""
    id = request.json.get('id')
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("DELETE FROM course_schedule WHERE id=%s", (id,))
        db.commit()
        return jsonify({"code": 200, "msg": "删除成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"code": 500, "msg": f"删除失败: {str(e)}"})
    finally:
        cur.close()
        db.close()

# ==================== 导出和提醒模块 ====================
@app.route("/api/schedule/export/excel")
def export_excel():
    """导出课表到Excel"""
    date = request.args.get("date")
    return jsonify({"code": 200, "data": []})

@app.route("/api/schedule/tomorrow")
def schedule_tomorrow():
    """获取明天的课程"""
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    db = get_db()
    cur = db.cursor()
    cur.execute('''SELECT s.name, s.parent_phone, t.name, cs.subject, cs.class_time
                   FROM course_schedule cs
                   JOIN student s ON cs.student_id=s.id
                   JOIN teacher t ON cs.teacher_id=t.id
                   WHERE cs.class_date=%s''', (tomorrow,))
    data = cur.fetchall()
    cur.close()
    db.close()
    result = [{"student_name": r[0], "parent_phone": r[1], "teacher_name": r[2], "subject": r[3], "class_time": r[4]} for r in data]
    return jsonify({"code": 200, "data": result})

# ------------------- 仪表盘数据 -------------------
@app.route("/api/dashboard/stats")
def dashboard_stats():
    """获取首页统计数据"""
    db = get_db()
    cur = db.cursor()
    
    cur.execute("SELECT COUNT(*) FROM student")
    student_count = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM teacher")
    teacher_count = cur.fetchone()[0]
    
    today = datetime.now().strftime("%Y-%m-%d")
    cur.execute("SELECT COUNT(*) FROM course_schedule WHERE class_date=%s", (today,))
    today_classes = cur.fetchone()[0]
    
    cur.execute("SELECT SUM(surplus) FROM course_package")
    total_surplus = cur.fetchone()[0] or 0
    
    cur.close()
    db.close()
    
    return jsonify({"code": 200, "data": {
        "student_count": student_count,
        "teacher_count": teacher_count,
        "today_classes": today_classes,
        "total_surplus_hours": float(total_surplus)
    }})

# ------------------- 启动应用 -------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
