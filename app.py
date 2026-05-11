from flask import Flask, request, jsonify
from flask_cors import CORS
import pg8000
import os
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

# Neon 云数据库配置
DB_CONFIG = {
    'host': 'ep-rapid-frog-ani7chkm.c-6.us-east-1.aws.neon.tech',
    'user': 'neondb_owner',
    'password': 'npg_b1QR9lMdusev',
    'database': 'neondb',
    'port': 5432
}

def get_db():
    """获取数据库连接"""
    try:
        conn = pg8000.connect(
            host=DB_CONFIG['host'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            database=DB_CONFIG['database'],
            port=DB_CONFIG['port'],
            ssl=True  # Neon 需要 SSL
        )
        return conn
    except Exception as e:
        print(f"数据库连接错误: {e}")
        raise e

# ------------------- 健康检查 -------------------
@app.route("/")
def index():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})

@app.route("/health")
def health():
    return jsonify({"status": "healthy"})

@app.route("/api/test-db")
def test_db():
    """测试数据库连接"""
    try:
        db = get_db()
        cur = db.cursor()
        cur.execute("SELECT NOW()")
        result = cur.fetchone()
        cur.close()
        db.close()
        return jsonify({"status": "success", "db_time": str(result[0])})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ------------------- 初始化数据库表 -------------------
@app.route("/api/init-db", methods=["POST"])
def init_db():
    """初始化数据库表结构"""
    try:
        db = get_db()
        cur = db.cursor()
        
        # 创建用户表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS "user" (
                id SERIAL PRIMARY KEY,
                phone VARCHAR(20) UNIQUE NOT NULL,
                password VARCHAR(100) NOT NULL,
                name VARCHAR(50),
                role VARCHAR(20) DEFAULT 'parent',
                status INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 创建学生表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS student (
                id SERIAL PRIMARY KEY,
                name VARCHAR(50) NOT NULL,
                phone VARCHAR(20),
                grade VARCHAR(20),
                school VARCHAR(100),
                parent_name VARCHAR(50),
                parent_phone VARCHAR(20),
                status INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 创建教师表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS teacher (
                id SERIAL PRIMARY KEY,
                name VARCHAR(50) NOT NULL,
                phone VARCHAR(20),
                subject VARCHAR(50),
                class_fee DECIMAL(10,2),
                status VARCHAR(20) DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 创建课时包表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS course_package (
                id SERIAL PRIMARY KEY,
                student_id INTEGER REFERENCES student(id),
                total DECIMAL(8,2) NOT NULL,
                used DECIMAL(8,2) DEFAULT 0,
                surplus DECIMAL(8,2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 创建排课表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS course_schedule (
                id SERIAL PRIMARY KEY,
                student_id INTEGER REFERENCES student(id),
                teacher_id INTEGER REFERENCES teacher(id),
                subject VARCHAR(100),
                class_date DATE,
                class_time VARCHAR(20),
                classroom VARCHAR(50),
                week_day INTEGER,
                repeat_type INTEGER DEFAULT 0,
                status VARCHAR(20) DEFAULT 'scheduled',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 插入测试数据
        cur.execute("SELECT COUNT(*) FROM \"user\"")
        user_count = cur.fetchone()[0]
        if user_count == 0:
            cur.execute("""
                INSERT INTO "user" (phone, password, name, role) 
                VALUES ('13800138000', '123456', '管理员', 'admin')
            """)
        
        cur.execute("SELECT COUNT(*) FROM teacher")
        teacher_count = cur.fetchone()[0]
        if teacher_count == 0:
            cur.execute("""
                INSERT INTO teacher (name, phone, subject, class_fee) 
                VALUES ('李老师', '13700137000', '数学', 150)
            """)
        
        cur.execute("SELECT COUNT(*) FROM student")
        student_count = cur.fetchone()[0]
        if student_count == 0:
            cur.execute("""
                INSERT INTO student (name, phone, grade, school) 
                VALUES ('张三', '13900139000', '三年级', '第一小学')
            """)
        
        db.commit()
        cur.close()
        db.close()
        
        return jsonify({
            "code": 200, 
            "msg": "数据库初始化成功",
            "data": {
                "users": user_count,
                "teachers": teacher_count,
                "students": student_count
            }
        })
    except Exception as e:
        return jsonify({"code": 500, "msg": f"初始化失败: {str(e)}"}), 500

# ==================== 用户管理模块 ====================
@app.route("/api/login", methods=["POST"])
def login():
    """用户登录"""
    try:
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
    except Exception as e:
        return jsonify({"code": 500, "msg": f"登录失败: {str(e)}"}), 500

@app.route("/api/user/list")
def user_list():
    """获取用户列表"""
    try:
        db = get_db()
        cur = db.cursor()
        cur.execute("SELECT id, phone, name, role, status, created_at FROM \"user\" ORDER BY id")
        data = cur.fetchall()
        cur.close()
        db.close()
        result = [{"id": r[0], "phone": r[1], "name": r[2], "role": r[3], 
                   "status": r[4], "created_at": str(r[5]) if r[5] else None} for r in data]
        return jsonify({"code": 200, "data": result})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500

# ==================== 学生管理模块 ====================
@app.route("/api/student/list")
def student_list():
    """获取学生列表"""
    try:
        db = get_db()
        cur = db.cursor()
        cur.execute("SELECT id, name, phone, grade, school FROM student ORDER BY id")
        data = cur.fetchall()
        cur.close()
        db.close()
        result = [{"id": r[0], "name": r[1], "phone": r[2], "grade": r[3], "school": r[4]} for r in data]
        return jsonify({"code": 200, "data": result})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500

@app.route("/api/student/add", methods=["POST"])
def student_add():
    """添加学生"""
    try:
        d = request.json
        db = get_db()
        cur = db.cursor()
        cur.execute("INSERT INTO student (name, phone, grade, school) VALUES (%s, %s, %s, %s)",
                    (d['name'], d.get('phone', ''), d.get('grade', ''), d.get('school', '')))
        db.commit()
        cur.close()
        db.close()
        return jsonify({"code": 200, "msg": "添加成功"})
    except Exception as e:
        return jsonify({"code": 500, "msg": f"添加失败: {str(e)}"}), 500

@app.route("/api/student/update", methods=["POST"])
def student_update():
    """更新学生信息"""
    try:
        d = request.json
        db = get_db()
        cur = db.cursor()
        cur.execute("UPDATE student SET name=%s, phone=%s, grade=%s, school=%s WHERE id=%s",
                    (d['name'], d.get('phone', ''), d.get('grade', ''), d.get('school', ''), d['id']))
        db.commit()
        cur.close()
        db.close()
        return jsonify({"code": 200, "msg": "更新成功"})
    except Exception as e:
        return jsonify({"code": 500, "msg": f"更新失败: {str(e)}"}), 500

@app.route("/api/student/delete", methods=["POST"])
def student_delete():
    """删除学生"""
    try:
        id = request.json.get('id')
        db = get_db()
        cur = db.cursor()
        cur.execute("DELETE FROM student WHERE id=%s", (id,))
        db.commit()
        cur.close()
        db.close()
        return jsonify({"code": 200, "msg": "删除成功"})
    except Exception as e:
        return jsonify({"code": 500, "msg": f"删除失败: {str(e)}"}), 500

# ==================== 教师管理模块 ====================
@app.route("/api/teacher/list")
def teacher_list():
    """获取教师列表"""
    try:
        db = get_db()
        cur = db.cursor()
        cur.execute("SELECT id, name, phone, subject, class_fee FROM teacher WHERE status='active' ORDER BY id")
        data = cur.fetchall()
        cur.close()
        db.close()
        result = [{"id": r[0], "name": r[1], "phone": r[2], "subject": r[3], 
                   "class_fee": float(r[4]) if r[4] else 0} for r in data]
        return jsonify({"code": 200, "data": result})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500

@app.route("/api/teacher/add", methods=["POST"])
def teacher_add():
    """添加教师"""
    try:
        d = request.json
        db = get_db()
        cur = db.cursor()
        cur.execute("INSERT INTO teacher (name, phone, subject, class_fee) VALUES (%s, %s, %s, %s)",
                    (d['name'], d.get('phone', ''), d.get('subject', ''), d.get('class_fee', 0)))
        db.commit()
        cur.close()
        db.close()
        return jsonify({"code": 200, "msg": "添加成功"})
    except Exception as e:
        return jsonify({"code": 500, "msg": f"添加失败: {str(e)}"}), 500

@app.route("/api/teacher/update", methods=["POST"])
def teacher_update():
    """更新教师信息"""
    try:
        d = request.json
        db = get_db()
        cur = db.cursor()
        cur.execute("UPDATE teacher SET name=%s, phone=%s, subject=%s, class_fee=%s WHERE id=%s",
                    (d['name'], d.get('phone', ''), d.get('subject', ''), d.get('class_fee', 0), d['id']))
        db.commit()
        cur.close()
        db.close()
        return jsonify({"code": 200, "msg": "更新成功"})
    except Exception as e:
        return jsonify({"code": 500, "msg": f"更新失败: {str(e)}"}), 500

@app.route("/api/teacher/delete", methods=["POST"])
def teacher_delete():
    """删除教师"""
    try:
        id = request.json.get('id')
        db = get_db()
        cur = db.cursor()
        cur.execute("DELETE FROM teacher WHERE id=%s", (id,))
        db.commit()
        cur.close()
        db.close()
        return jsonify({"code": 200, "msg": "删除成功"})
    except Exception as e:
        return jsonify({"code": 500, "msg": f"删除失败: {str(e)}"}), 500

# ==================== 课时管理模块 ====================
@app.route("/api/course/list")
def course_list():
    """获取课时包列表"""
    try:
        db = get_db()
        cur = db.cursor()
        cur.execute("""
            SELECT cp.id, s.name, cp.total, cp.used, cp.surplus 
            FROM course_package cp 
            JOIN student s ON cp.student_id=s.id 
            ORDER BY cp.id DESC
        """)
        data = cur.fetchall()
        cur.close()
        db.close()
        result = [{"id": r[0], "student_name": r[1], "total": float(r[2]) if r[2] else 0,
                   "used": float(r[3]) if r[3] else 0, "surplus": float(r[4]) if r[4] else 0} for r in data]
        return jsonify({"code": 200, "data": result})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500

@app.route("/api/course/add", methods=["POST"])
def course_add():
    """添加课时包"""
    try:
        d = request.json
        db = get_db()
        cur = db.cursor()
        cur.execute("INSERT INTO course_package (student_id, total, used, surplus) VALUES (%s, %s, 0, %s)",
                    (d['student_id'], d['total'], d['total']))
        db.commit()
        cur.close()
        db.close()
        return jsonify({"code": 200, "msg": "添加成功"})
    except Exception as e:
        return jsonify({"code": 500, "msg": f"添加失败: {str(e)}"}), 500

# ==================== 排课管理模块 ====================
@app.route("/api/schedule/calendar")
def schedule_calendar():
    """获取日历排课数据"""
    try:
        date = request.args.get('date')
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")
        
        db = get_db()
        cur = db.cursor()
        cur.execute("""
            SELECT cs.id, s.name, t.name, cs.subject, cs.class_time, cs.classroom, cs.status
            FROM course_schedule cs
            LEFT JOIN student s ON cs.student_id=s.id
            LEFT JOIN teacher t ON cs.teacher_id=t.id
            WHERE cs.class_date=%s
            ORDER BY cs.class_time
        """, (date,))
        data = cur.fetchall()
        cur.close()
        db.close()
        result = [{"id": r[0], "student_name": r[1] or "集体课", "teacher_name": r[2] or "待分配",
                   "subject": r[3], "class_time": r[4], "classroom": r[5], "status": r[6]} for r in data]
        return jsonify({"code": 200, "data": result})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500

@app.route("/api/schedule/check", methods=["POST"])
def schedule_check():
    """检查排课冲突"""
    try:
        d = request.json
        db = get_db()
        cur = db.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM course_schedule
            WHERE (teacher_id=%s OR classroom=%s)
            AND class_date=%s AND class_time=%s AND id!=%s
        """, (d.get('tid'), d.get('room'), d['date'], d['time'], d.get('id', 0)))
        cnt = cur.fetchone()[0]
        cur.close()
        db.close()
        return jsonify({"code": 200, "conflict": cnt > 0})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500

@app.route("/api/schedule/save", methods=["POST"])
def schedule_save():
    """保存排课"""
    try:
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
        
        for day in days:
            wd = datetime.strptime(day, "%Y-%m-%d").weekday() + 1
            cur.execute("""
                INSERT INTO course_schedule
                (student_id, teacher_id, subject, class_date, class_time, classroom, week_day, repeat_type)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (d.get('sid'), d.get('tid'), d['subject'], day, d['time'], d['room'], wd, d.get('repeat', 0)))
        
        db.commit()
        cur.close()
        db.close()
        return jsonify({"code": 200, "msg": f"成功添加{len(days)}节课"})
    except Exception as e:
        return jsonify({"code": 500, "msg": f"保存失败: {str(e)}"}), 500

@app.route("/api/schedule/update", methods=["POST"])
def schedule_update():
    """更新排课"""
    try:
        d = request.json
        db = get_db()
        cur = db.cursor()
        cur.execute("""
            UPDATE course_schedule 
            SET student_id=%s, teacher_id=%s, subject=%s, class_date=%s, class_time=%s, classroom=%s 
            WHERE id=%s
        """, (d.get('sid'), d.get('tid'), d['subject'], d['date'], d['time'], d['room'], d['id']))
        db.commit()
        cur.close()
        db.close()
        return jsonify({"code": 200, "msg": "更新成功"})
    except Exception as e:
        return jsonify({"code": 500, "msg": f"更新失败: {str(e)}"}), 500

@app.route("/api/schedule/delete", methods=["POST"])
def schedule_delete():
    """删除排课"""
    try:
        id = request.json.get('id')
        db = get_db()
        cur = db.cursor()
        cur.execute("DELETE FROM course_schedule WHERE id=%s", (id,))
        db.commit()
        cur.close()
        db.close()
        return jsonify({"code": 200, "msg": "删除成功"})
    except Exception as e:
        return jsonify({"code": 500, "msg": f"删除失败: {str(e)}"}), 500

# ==================== 提醒和报表模块 ====================
@app.route("/api/schedule/tomorrow")
def schedule_tomorrow():
    """获取明天的课程"""
    try:
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        db = get_db()
        cur = db.cursor()
        cur.execute("""
            SELECT s.name, s.parent_phone, t.name, cs.subject, cs.class_time, cs.classroom
            FROM course_schedule cs
            LEFT JOIN student s ON cs.student_id=s.id
            LEFT JOIN teacher t ON cs.teacher_id=t.id
            WHERE cs.class_date=%s AND cs.status='scheduled'
            ORDER BY cs.class_time
        """, (tomorrow,))
        data = cur.fetchall()
        cur.close()
        db.close()
        result = [{"student_name": r[0] or "集体课", "parent_phone": r[1], 
                   "teacher_name": r[2] or "待分配", "subject": r[3], 
                   "class_time": r[4], "classroom": r[5]} for r in data]
        return jsonify({"code": 200, "data": result, "date": tomorrow})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500

@app.route("/api/schedule/export/excel")
def export_excel():
    """导出课表（简化版）"""
    return jsonify({"code": 200, "msg": "导出功能开发中", "data": []})

# ==================== 仪表盘数据 ====================
@app.route("/api/dashboard/stats")
def dashboard_stats():
    """获取首页统计数据"""
    try:
        db = get_db()
        cur = db.cursor()
        
        cur.execute("SELECT COUNT(*) FROM student")
        student_count = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM teacher")
        teacher_count = cur.fetchone()[0]
        
        today = datetime.now().strftime("%Y-%m-%d")
        cur.execute("SELECT COUNT(*) FROM course_schedule WHERE class_date=%s", (today,))
        today_classes = cur.fetchone()[0]
        
        cur.execute("SELECT COALESCE(SUM(surplus), 0) FROM course_package")
        total_surplus = cur.fetchone()[0] or 0
        
        cur.close()
        db.close()
        
        return jsonify({"code": 200, "data": {
            "student_count": student_count,
            "teacher_count": teacher_count,
            "today_classes": today_classes,
            "total_surplus_hours": float(total_surplus)
        }})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500

# ------------------- 启动应用 -------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
