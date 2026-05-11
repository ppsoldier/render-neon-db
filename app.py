from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import psycopg2
import os
from datetime import datetime, timedelta
import io
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill
import json

app = Flask(__name__)
CORS(app)

def get_db():
    """获取数据库连接"""
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        port=5432,
        sslmode="require"
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
    """获取学生列表（支持分页和搜索）"""
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    keyword = request.args.get('keyword', '')
    
    db = get_db()
    cur = db.cursor()
    
    # 构建查询条件
    sql = "SELECT id, name, phone, grade, school, parent_name, status, created_at FROM student WHERE 1=1"
    params = []
    if keyword:
        sql += " AND (name LIKE %s OR phone LIKE %s)"
        params.extend([f'%{keyword}%', f'%{keyword}%'])
    sql += " ORDER BY id DESC LIMIT %s OFFSET %s"
    params.extend([limit, (page-1)*limit])
    
    cur.execute(sql, params)
    data = cur.fetchall()
    
    # 获取总数
    count_sql = "SELECT COUNT(*) FROM student WHERE 1=1"
    count_params = []
    if keyword:
        count_sql += " AND (name LIKE %s OR phone LIKE %s)"
        count_params.extend([f'%{keyword}%', f'%{keyword}%'])
    cur.execute(count_sql, count_params)
    total = cur.fetchone()[0]
    
    cur.close()
    db.close()
    
    result = [{"id": r[0], "name": r[1], "phone": r[2], "grade": r[3], 
               "school": r[4], "parent_name": r[5], "status": r[6], 
               "created_at": str(r[7]) if r[7] else None} for r in data]
    return jsonify({"code": 200, "data": result, "total": total, "page": page})

@app.route("/api/student/detail/<int:student_id>")
def student_detail(student_id):
    """获取学生详情"""
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id, name, phone, grade, school, parent_name, parent_phone, address, birthday, status FROM student WHERE id=%s", (student_id,))
    data = cur.fetchone()
    cur.close()
    db.close()
    
    if data:
        return jsonify({"code": 200, "data": {
            "id": data[0], "name": data[1], "phone": data[2], "grade": data[3],
            "school": data[4], "parent_name": data[5], "parent_phone": data[6],
            "address": data[7], "birthday": str(data[8]) if data[8] else None, "status": data[9]
        }})
    return jsonify({"code": 404, "msg": "学生不存在"})

@app.route("/api/student/add", methods=["POST"])
def student_add():
    """添加学生"""
    d = request.json
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("""INSERT INTO student (name, phone, grade, school, parent_name, parent_phone, address, birthday) 
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (d['name'], d.get('phone', ''), d.get('grade', ''), d.get('school', ''),
                     d.get('parent_name', ''), d.get('parent_phone', ''), d.get('address', ''),
                     d.get('birthday')))
        db.commit()
        return jsonify({"code": 200, "msg": "添加成功", "id": cur.lastrowid})
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
        cur.execute("""UPDATE student SET name=%s, phone=%s, grade=%s, school=%s, 
                       parent_name=%s, parent_phone=%s, address=%s, birthday=%s, status=%s WHERE id=%s""",
                    (d['name'], d.get('phone', ''), d.get('grade', ''), d.get('school', ''),
                     d.get('parent_name', ''), d.get('parent_phone', ''), d.get('address', ''),
                     d.get('birthday'), d.get('status', 1), d['id']))
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
    status = request.args.get('status', 'active')
    db = get_db()
    cur = db.cursor()
    if status == 'all':
        cur.execute("SELECT id, name, phone, subject, class_fee, status, hire_date FROM teacher ORDER BY id")
    else:
        cur.execute("SELECT id, name, phone, subject, class_fee, status, hire_date FROM teacher WHERE status='active' ORDER BY id")
    data = cur.fetchall()
    cur.close()
    db.close()
    
    result = [{"id": r[0], "name": r[1], "phone": r[2], "subject": r[3], 
               "class_fee": float(r[4]) if r[4] else 0, "status": r[5],
               "hire_date": str(r[6]) if r[6] else None} for r in data]
    return jsonify({"code": 200, "data": result})

@app.route("/api/teacher/detail/<int:teacher_id>")
def teacher_detail(teacher_id):
    """获取教师详情"""
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id, name, phone, subject, class_fee, qualification, bank_card, status, hire_date FROM teacher WHERE id=%s", (teacher_id,))
    data = cur.fetchone()
    cur.close()
    db.close()
    
    if data:
        return jsonify({"code": 200, "data": {
            "id": data[0], "name": data[1], "phone": data[2], "subject": data[3],
            "class_fee": float(data[4]) if data[4] else 0, "qualification": data[5],
            "bank_card": data[6], "status": data[7], "hire_date": str(data[8]) if data[8] else None
        }})
    return jsonify({"code": 404, "msg": "教师不存在"})

@app.route("/api/teacher/add", methods=["POST"])
def teacher_add():
    """添加教师"""
    d = request.json
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("""INSERT INTO teacher (name, phone, subject, class_fee, qualification, bank_card, hire_date) 
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (d['name'], d.get('phone', ''), d.get('subject', ''), d.get('class_fee', 0),
                     d.get('qualification', ''), d.get('bank_card', ''), d.get('hire_date')))
        db.commit()
        return jsonify({"code": 200, "msg": "添加成功", "id": cur.lastrowid})
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
        cur.execute("""UPDATE teacher SET name=%s, phone=%s, subject=%s, class_fee=%s, 
                       qualification=%s, bank_card=%s, status=%s, hire_date=%s WHERE id=%s""",
                    (d['name'], d.get('phone', ''), d.get('subject', ''), d.get('class_fee', 0),
                     d.get('qualification', ''), d.get('bank_card', ''), d.get('status', 'active'),
                     d.get('hire_date'), d['id']))
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
    student_id = request.args.get('student_id', type=int)
    db = get_db()
    cur = db.cursor()
    
    if student_id:
        cur.execute("""SELECT cp.id, s.name, cp.total, cp.used, cp.surplus, cp.course_name, cp.expire_date, cp.status 
                       FROM course_package cp 
                       JOIN student s ON cp.student_id=s.id 
                       WHERE cp.student_id=%s 
                       ORDER BY cp.id DESC""", (student_id,))
    else:
        cur.execute("""SELECT cp.id, s.name, cp.total, cp.used, cp.surplus, cp.course_name, cp.expire_date, cp.status 
                       FROM course_package cp 
                       JOIN student s ON cp.student_id=s.id 
                       ORDER BY cp.id DESC""")
    
    data = cur.fetchall()
    cur.close()
    db.close()
    
    result = [{"id": r[0], "student_name": r[1], "total": float(r[2]) if r[2] else 0,
               "used": float(r[3]) if r[3] else 0, "surplus": float(r[4]) if r[4] else 0,
               "course_name": r[5], "expire_date": str(r[6]) if r[6] else None, "status": r[7]} for r in data]
    return jsonify({"code": 200, "data": result})

@app.route("/api/course/statistics")
def course_statistics():
    """课时统计汇总"""
    db = get_db()
    cur = db.cursor()
    
    # 总课时统计
    cur.execute("SELECT SUM(total), SUM(used), SUM(surplus) FROM course_package WHERE status='active'")
    total_data = cur.fetchone()
    
    # 按班级统计
    cur.execute("""SELECT s.grade, SUM(cp.surplus) 
                   FROM course_package cp 
                   JOIN student s ON cp.student_id=s.id 
                   WHERE cp.status='active' 
                   GROUP BY s.grade""")
    grade_stats = cur.fetchall()
    
    cur.close()
    db.close()
    
    return jsonify({"code": 200, "data": {
        "total_hours": float(total_data[0]) if total_data[0] else 0,
        "used_hours": float(total_data[1]) if total_data[1] else 0,
        "surplus_hours": float(total_data[2]) if total_data[2] else 0,
        "grade_stats": [{"grade": g[0], "surplus": float(g[1])} for g in grade_stats]
    }})

@app.route("/api/course/add", methods=["POST"])
def course_add():
    """添加课时包"""
    d = request.json
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("""INSERT INTO course_package (student_id, total, used, surplus, course_name, expire_date) 
                       VALUES (%s, %s, 0, %s, %s, %s)""",
                    (d['student_id'], d['total'], d['total'], d.get('course_name', '标准课程'), d.get('expire_date')))
        db.commit()
        return jsonify({"code": 200, "msg": "添加成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"code": 500, "msg": f"添加失败: {str(e)}"})
    finally:
        cur.close()
        db.close()

@app.route("/api/course/consume", methods=["POST"])
def course_consume():
    """扣除课时"""
    d = request.json
    db = get_db()
    cur = db.cursor()
    try:
        # 检查剩余课时
        cur.execute("SELECT surplus FROM course_package WHERE id=%s", (d['package_id'],))
        surplus = cur.fetchone()
        if not surplus or surplus[0] < d['hours']:
            return jsonify({"code": 400, "msg": "课时不足"})
        
        # 扣除课时
        cur.execute("UPDATE course_package SET used=used+%s, surplus=surplus-%s WHERE id=%s",
                    (d['hours'], d['hours'], d['package_id']))
        
        # 记录消费记录
        cur.execute("""INSERT INTO hour_consumption (package_id, schedule_id, hours, consume_date) 
                       VALUES (%s, %s, %s, %s)""",
                    (d['package_id'], d.get('schedule_id'), d['hours'], datetime.now().date()))
        
        db.commit()
        return jsonify({"code": 200, "msg": f"扣除{d['hours']}课时成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"code": 500, "msg": f"扣除失败: {str(e)}"})
    finally:
        cur.close()
        db.close()

@app.route("/api/course/export")
def course_export():
    """导出课时统计报表"""
    db = get_db()
    cur = db.cursor()
    cur.execute("""SELECT s.name, s.grade, cp.course_name, cp.total, cp.used, cp.surplus, cp.expire_date 
                   FROM course_package cp 
                   JOIN student s ON cp.student_id=s.id 
                   WHERE cp.status='active' 
                   ORDER BY s.grade, s.name""")
    data = cur.fetchall()
    cur.close()
    db.close()
    
    # 创建Excel文件
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "课时统计报表"
    
    # 设置表头
    headers = ["学生姓名", "年级", "课程名称", "总课时", "已用课时", "剩余课时", "有效期"]
    ws.append(headers)
    
    # 设置表头样式
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        cell.font = Font(color="FFFFFF")
    
    # 写入数据
    for row in data:
        ws.append([row[0], row[1], row[2], float(row[3]) if row[3] else 0,
                   float(row[4]) if row[4] else 0, float(row[5]) if row[5] else 0, str(row[6]) if row[6] else ""])
    
    # 调整列宽
    for col in ws.columns:
        max_length = 0
        col_letter = col[0].column_letter
        for cell in col:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        adjusted_width = min(max_length + 2, 30)
        ws.column_dimensions[col_letter].width = adjusted_width
    
    # 保存到内存
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name='课时统计报表.xlsx')

# ==================== 排课管理模块 ====================
@app.route("/api/schedule/calendar")
def schedule_calendar():
    """获取日历排课数据"""
    date = request.args.get('date')
    if not date:
        return jsonify({"code": 400, "msg": "缺少日期参数"})
    
    db = get_db()
    cur = db.cursor()
    cur.execute('''SELECT cs.id, s.name, t.name, cs.subject, cs.class_time, cs.classroom, cs.status
                   FROM course_schedule cs
                   LEFT JOIN student s ON cs.student_id=s.id
                   LEFT JOIN teacher t ON cs.teacher_id=t.id
                   WHERE cs.class_date=%s
                   ORDER BY cs.class_time''', (date,))
    data = cur.fetchall()
    cur.close()
    db.close()
    
    result = [{"id": r[0], "student_name": r[1] or "集体课", "teacher_name": r[2] or "待分配",
               "subject": r[3], "class_time": r[4], "classroom": r[5], "status": r[6] or "scheduled"} for r in data]
    return jsonify({"code": 200, "data": result})

@app.route("/api/schedule/list")
def schedule_list():
    """获取排课列表（支持日期范围）"""
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    teacher_id = request.args.get('teacher_id', type=int)
    
    db = get_db()
    cur = db.cursor()
    
    sql = '''SELECT cs.id, s.name, t.name, cs.subject, cs.class_date, cs.class_time, cs.classroom, cs.status
             FROM course_schedule cs
             LEFT JOIN student s ON cs.student_id=s.id
             LEFT JOIN teacher t ON cs.teacher_id=t.id
             WHERE 1=1'''
    params = []
    
    if start_date:
        sql += " AND cs.class_date >= %s"
        params.append(start_date)
    if end_date:
        sql += " AND cs.class_date <= %s"
        params.append(end_date)
    if teacher_id:
        sql += " AND cs.teacher_id = %s"
        params.append(teacher_id)
    
    sql += " ORDER BY cs.class_date, cs.class_time"
    
    cur.execute(sql, params)
    data = cur.fetchall()
    cur.close()
    db.close()
    
    result = [{"id": r[0], "student_name": r[1] or "集体课", "teacher_name": r[2] or "待分配",
               "subject": r[3], "class_date": str(r[4]), "class_time": r[5],
               "classroom": r[6], "status": r[7]} for r in data]
    return jsonify({"code": 200, "data": result})

@app.route("/api/schedule/check", methods=["POST"])
def schedule_check():
    """检查排课冲突"""
    d = request.json
    db = get_db()
    cur = db.cursor()
    
    # 检查教师或教室在同一时间是否有课
    cur.execute('''SELECT COUNT(*) FROM course_schedule
                   WHERE (teacher_id=%s OR classroom=%s)
                   AND class_date=%s AND class_time=%s AND id != %s''',
                (d.get('tid'), d.get('room'), d['date'], d['time'], d.get('id', 0)))
    cnt = cur.fetchone()[0]
    
    cur.close()
    db.close()
    return jsonify({"code": 200, "conflict": cnt > 0, "conflict_count": cnt})

@app.route("/api/schedule/save", methods=["POST"])
def schedule_save():
    """保存排课（支持每周重复）"""
    d = request.json
    db = get_db()
    cur = db.cursor()
    
    days = []
    if int(d.get('repeat', 0)) == 1:
        base = datetime.strptime(d['date'], "%Y-%m-%d")
        for i in range(8):  # 重复8周
            days.append((base + timedelta(days=i*7)).strftime("%Y-%m-%d"))
    else:
        days.append(d['date'])
    
    try:
        for day in days:
            wd = datetime.strptime(day, "%Y-%m-%d").weekday() + 1
            cur.execute('''INSERT INTO course_schedule
                           (student_id, teacher_id, subject, class_date, class_time, classroom, week_day, repeat_type, status)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                        (d.get('sid'), d.get('tid'), d['subject'], day, d['time'], 
                         d['room'], wd, d.get('repeat', 0), 'scheduled'))
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
                       SET student_id=%s, teacher_id=%s, subject=%s, class_date=%s, 
                           class_time=%s, classroom=%s, status=%s 
                       WHERE id=%s''',
                    (d.get('sid'), d.get('tid'), d['subject'], d['date'], 
                     d['time'], d['room'], d.get('status', 'scheduled'), d['id']))
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

@app.route("/api/schedule/complete", methods=["POST"])
def schedule_complete():
    """标记课程完成"""
    d = request.json
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("UPDATE course_schedule SET status='completed' WHERE id=%s", (d['id'],))
        db.commit()
        return jsonify({"code": 200, "msg": "课程已标记完成"})
    except Exception as e:
        db.rollback()
        return jsonify({"code": 500, "msg": f"操作失败: {str(e)}"})
    finally:
        cur.close()
        db.close()

# ==================== 教师课酬统计模块 ====================
@app.route("/api/salary/statistics")
def salary_statistics():
    """教师课酬统计"""
    teacher_id = request.args.get('teacher_id', type=int)
    month = request.args.get('month')  # 格式: 2024-03
    
    db = get_db()
    cur = db.cursor()
    
    sql = '''SELECT t.id, t.name, t.class_fee, 
                    COUNT(cs.id) as class_count,
                    SUM(CASE WHEN cs.status='completed' THEN 1 ELSE 0 END) as completed_count,
                    COALESCE(SUM(CASE WHEN cs.status='completed' THEN CAST(cs.duration AS DECIMAL) ELSE 0 END), 0) as total_hours
             FROM teacher t
             LEFT JOIN course_schedule cs ON t.id = cs.teacher_id
             WHERE 1=1'''
    params = []
    
    if teacher_id:
        sql += " AND t.id = %s"
        params.append(teacher_id)
    if month:
        sql += " AND TO_CHAR(cs.class_date, 'YYYY-MM') = %s"
        params.append(month)
    
    sql += " GROUP BY t.id, t.name, t.class_fee ORDER BY t.id"
    
    cur.execute(sql, params)
    data = cur.fetchall()
    
    # 获取已发放课酬记录
    payment_sql = "SELECT teacher_id, SUM(amount) as paid FROM teacher_payment WHERE 1=1"
    payment_params = []
    if teacher_id:
        payment_sql += " AND teacher_id = %s"
        payment_params.append(teacher_id)
    if month:
        payment_sql += " AND month = %s"
        payment_params.append(month)
    payment_sql += " GROUP BY teacher_id"
    
    cur.execute(payment_sql, payment_params)
    payments = {p[0]: float(p[1]) for p in cur.fetchall()}
    
    cur.close()
    db.close()
    
    result = []
    for r in data:
        total_amount = float(r[2] or 0) * float(r[4] or 0) if r[2] and r[4] else 0
        result.append({
            "teacher_id": r[0],
            "teacher_name": r[1],
            "class_fee": float(r[2]) if r[2] else 0,
            "class_count": r[3],
            "completed_count": r[4],
            "total_hours": float(r[4]) if r[4] else 0,
            "total_amount": total_amount,
            "paid_amount": payments.get(r[0], 0),
            "unpaid_amount": total_amount - payments.get(r[0], 0)
        })
    
    return jsonify({"code": 200, "data": result})

@app.route("/api/salary/record", methods=["POST"])
def salary_record():
    """记录发放课酬"""
    d = request.json
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("""INSERT INTO teacher_payment (teacher_id, month, amount, status, pay_date, notes) 
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (d['teacher_id'], d['month'], d['amount'], d.get('status', 'paid'), 
                     datetime.now().date(), d.get('notes', '')))
        db.commit()
        return jsonify({"code": 200, "msg": "记录成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"code": 500, "msg": f"记录失败: {str(e)}"})
    finally:
        cur.close()
        db.close()

# ==================== 考勤管理模块 ====================
@app.route("/api/attendance/list")
def attendance_list():
    """获取考勤记录"""
    schedule_id = request.args.get('schedule_id', type=int)
    class_date = request.args.get('class_date')
    
    db = get_db()
    cur = db.cursor()
    
    if schedule_id:
        cur.execute("""SELECT a.id, s.name, a.status, a.checkin_time, a.notes
                       FROM attendance a
                       JOIN student s ON a.student_id = s.id
                       WHERE a.schedule_id = %s""", (schedule_id,))
    elif class_date:
        cur.execute("""SELECT a.id, s.name, a.status, a.checkin_time, cs.subject, cs.class_time
                       FROM attendance a
                       JOIN student s ON a.student_id = s.id
                       JOIN course_schedule cs ON a.schedule_id = cs.id
                       WHERE cs.class_date = %s
                       ORDER BY cs.class_time, s.name""", (class_date,))
    else:
        cur.execute("""SELECT a.id, s.name, a.status, a.checkin_time, cs.class_date, cs.subject
                       FROM attendance a
                       JOIN student s ON a.student_id = s.id
                       JOIN course_schedule cs ON a.schedule_id = cs.id
                       ORDER BY cs.class_date DESC, cs.class_time
                       LIMIT 100""")
    
    data = cur.fetchall()
    cur.close()
    db.close()
    
    result = [{"id": r[0], "student_name": r[1], "status": r[2], 
               "checkin_time": str(r[3]) if r[3] else None, "notes": r[4] if len(r) > 4 else None} 
              for r in data]
    return jsonify({"code": 200, "data": result})

@app.route("/api/attendance/mark", methods=["POST"])
def attendance_mark():
    """标记考勤"""
    d = request.json
    db = get_db()
    cur = db.cursor()
    try:
        # 检查是否已存在记录
        cur.execute("SELECT id FROM attendance WHERE schedule_id=%s AND student_id=%s",
                    (d['schedule_id'], d['student_id']))
        existing = cur.fetchone()
        
        if existing:
            cur.execute("""UPDATE attendance 
                           SET status=%s, checkin_time=NOW(), notes=%s 
                           WHERE schedule_id=%s AND student_id=%s""",
                        (d['status'], d.get('notes', ''), d['schedule_id'], d['student_id']))
        else:
            cur.execute("""INSERT INTO attendance (schedule_id, student_id, status, checkin_time, notes) 
                           VALUES (%s, %s, %s, NOW(), %s)""",
                        (d['schedule_id'], d['student_id'], d['status'], d.get('notes', '')))
        
        db.commit()
        return jsonify({"code": 200, "msg": "考勤记录成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"code": 500, "msg": f"记录失败: {str(e)}"})
    finally:
        cur.close()
        db.close()

@ -2467,7 +2467,7 @@
