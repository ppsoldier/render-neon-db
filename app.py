from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import pg8000
import os
from datetime import datetime, timedelta
import io
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill
import json
import traceback

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
        import ssl
        ssl_context = ssl.create_default_context()
        
        conn = pg8000.connect(
            host=DB_CONFIG['host'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            database=DB_CONFIG['database'],
            port=DB_CONFIG['port'],
            ssl_context=ssl_context
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
@app.route("/api/init-db", methods=["GET", "POST"])
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
                hire_date DATE,
                qualification TEXT,
                bank_card VARCHAR(50),
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
                course_name VARCHAR(100),
                expire_date DATE,
                status VARCHAR(20) DEFAULT 'active',
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
                class_time VARCHAR(30),
                classroom VARCHAR(50),
                student_ids TEXT,
                duration DECIMAL(5,2) DEFAULT 2,
                status VARCHAR(20) DEFAULT 'scheduled',
                repeat_type INTEGER DEFAULT 0,
                week_day INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 创建索引
        cur.execute("CREATE INDEX IF NOT EXISTS idx_cs_class_date ON course_schedule(class_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_cs_teacher_id ON course_schedule(teacher_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_cs_status ON course_schedule(status)")
        
        # 插入测试数据 - 用户
        cur.execute("SELECT COUNT(*) FROM \"user\"")
        user_count = cur.fetchone()[0]
        if user_count == 0:
            cur.execute("""
                INSERT INTO "user" (phone, password, name, role) 
                VALUES ('13800138000', '123456', '管理员', 'admin')
            """)
        
        # 插入测试数据 - 教师
        cur.execute("SELECT COUNT(*) FROM teacher")
        teacher_count = cur.fetchone()[0]
        if teacher_count == 0:
            cur.execute("""
                INSERT INTO teacher (name, phone, subject, class_fee) 
                VALUES ('李老师', '13700137000', '数学', 150)
            """)
            cur.execute("""
                INSERT INTO teacher (name, phone, subject, class_fee) 
                VALUES ('王老师', '13800138001', '语文', 160)
            """)
            cur.execute("""
                INSERT INTO teacher (name, phone, subject, class_fee) 
                VALUES ('陈春梅', '13900139002', '数学', 180)
            """)
        
        # 插入测试数据 - 学生
        cur.execute("SELECT COUNT(*) FROM student")
        student_count = cur.fetchone()[0]
        if student_count == 0:
            cur.execute("""
                INSERT INTO student (name, phone, grade, school) 
                VALUES ('张三', '13900139000', '三年级', '第一小学')
            """)
            cur.execute("""
                INSERT INTO student (name, phone, grade, school) 
                VALUES ('李四', '13900139001', '四年级', '第二小学')
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

@app.route("/api/user/add", methods=["POST"])
def user_add():
    """添加用户"""
    try:
        d = request.json
        db = get_db()
        cur = db.cursor()
        cur.execute("INSERT INTO \"user\" (phone, password, name, role, status) VALUES (%s, %s, %s, %s, %s)",
                    (d['phone'], d['password'], d['name'], d['role'], d.get('status', 1)))
        db.commit()
        cur.close()
        db.close()
        return jsonify({"code": 200, "msg": "添加成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"code": 500, "msg": f"添加失败: {str(e)}"}), 500

@app.route("/api/user/update", methods=["POST"])
def user_update():
    """更新用户信息"""
    try:
        d = request.json
        db = get_db()
        cur = db.cursor()
        cur.execute("UPDATE \"user\" SET phone=%s, name=%s, role=%s, status=%s WHERE id=%s",
                    (d['phone'], d['name'], d['role'], d.get('status', 1), d['id']))
        db.commit()
        cur.close()
        db.close()
        return jsonify({"code": 200, "msg": "更新成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"code": 500, "msg": f"更新失败: {str(e)}"}), 500

@app.route("/api/user/delete", methods=["POST"])
def user_delete():
    """删除用户"""
    try:
        id = request.json.get('id')
        db = get_db()
        cur = db.cursor()
        cur.execute("DELETE FROM \"user\" WHERE id=%s", (id,))
        db.commit()
        cur.close()
        db.close()
        return jsonify({"code": 200, "msg": "删除成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"code": 500, "msg": f"删除失败: {str(e)}"}), 500

# ==================== 学生管理模块 ====================
@app.route("/api/student/list")
def student_list():
    """获取学生列表"""
    try:
        db = get_db()
        cur = db.cursor()
        cur.execute("SELECT id, name, phone, grade, school FROM student ORDER BY id DESC")
        data = cur.fetchall()
        cur.close()
        db.close()
        result = [{"id": r[0], "name": r[1], "phone": r[2] or '', "grade": r[3] or '', "school": r[4] or ''} for r in data]
        return jsonify({"code": 200, "data": result})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500

@app.route("/api/student/detail/<int:student_id>", methods=["GET"])
def student_detail(student_id):
    """获取学生详情"""
    try:
        db = get_db()
        cur = db.cursor()
        cur.execute("""
            SELECT id, name, phone, grade, school, parent_name, parent_phone 
            FROM student WHERE id=%s
        """, (student_id,))
        data = cur.fetchone()
        cur.close()
        db.close()
        
        if data:
            return jsonify({
                "code": 200,
                "data": {
                    "id": data[0],
                    "name": data[1] or '',
                    "phone": data[2] or '',
                    "grade": data[3] or '',
                    "school": data[4] or '',
                    "parent_name": data[5] or '',
                    "parent_phone": data[6] or ''
                }
            })
        return jsonify({"code": 404, "msg": "学生不存在"})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500

@app.route("/api/student/add", methods=["POST"])
def student_add():
    """添加学生"""
    try:
        d = request.json
        db = get_db()
        cur = db.cursor()
        cur.execute("""
            INSERT INTO student (name, phone, grade, school, parent_name, parent_phone) 
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            d.get('name', ''), 
            d.get('phone', ''), 
            d.get('grade', ''), 
            d.get('school', ''),
            d.get('parent_name', ''),
            d.get('parent_phone', '')
        ))
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
        cur.execute("""
            UPDATE student 
            SET name=%s, phone=%s, grade=%s, school=%s, parent_name=%s, parent_phone=%s 
            WHERE id=%s
        """, (
            d.get('name', ''),
            d.get('phone', ''),
            d.get('grade', ''),
            d.get('school', ''),
            d.get('parent_name', ''),
            d.get('parent_phone', ''),
            d.get('id')
        ))
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
        data = request.json
        student_id = data.get('id')
        db = get_db()
        cur = db.cursor()
        cur.execute("DELETE FROM student WHERE id=%s", (student_id,))
        db.commit()
        cur.close()
        db.close()
        return jsonify({"code": 200, "msg": "删除成功"})
    except Exception as e:
        return jsonify({"code": 500, "msg": f"删除失败: {str(e)}"}), 500

# ==================== 教师管理模块 ====================
@app.route("/api/teacher/list", methods=["GET"])
def teacher_list():
    """获取教师列表"""
    try:
        db = get_db()
        cur = db.cursor()
        cur.execute("SELECT id, name, phone, subject, class_fee FROM teacher WHERE status='active' ORDER BY id DESC")
        data = cur.fetchall()
        cur.close()
        db.close()
        result = [{"id": r[0], "name": r[1] or '', "phone": r[2] or '', "subject": r[3] or '', "class_fee": float(r[4]) if r[4] else 0} for r in data]
        return jsonify({"code": 200, "data": result})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500

@app.route("/api/teacher/detail/<int:teacher_id>", methods=["GET"])
def teacher_detail(teacher_id):
    """获取教师详情"""
    try:
        db = get_db()
        cur = db.cursor()
        cur.execute("SELECT id, name, phone, subject, class_fee FROM teacher WHERE id=%s", (teacher_id,))
        data = cur.fetchone()
        cur.close()
        db.close()
        
        if data:
            return jsonify({
                "code": 200,
                "data": {
                    "id": data[0],
                    "name": data[1] or '',
                    "phone": data[2] or '',
                    "subject": data[3] or '',
                    "class_fee": float(data[4]) if data[4] else 0
                }
            })
        return jsonify({"code": 404, "msg": "教师不存在"})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500

@app.route("/api/teacher/add", methods=["POST"])
def teacher_add():
    """添加教师"""
    try:
        d = request.json
        db = get_db()
        cur = db.cursor()
        cur.execute("""
            INSERT INTO teacher (name, phone, subject, class_fee, status) 
            VALUES (%s, %s, %s, %s, 'active')
        """, (d.get('name', ''), d.get('phone', ''), d.get('subject', ''), d.get('class_fee', 0)))
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
        cur.execute("""
            UPDATE teacher 
            SET name=%s, phone=%s, subject=%s, class_fee=%s 
            WHERE id=%s
        """, (d.get('name', ''), d.get('phone', ''), d.get('subject', ''), d.get('class_fee', 0), d.get('id')))
        db.commit()
        cur.close()
        db.close()
        return jsonify({"code": 200, "msg": "更新成功"})
    except Exception as e:
        return jsonify({"code": 500, "msg": f"更新失败: {str(e)}"}), 500

@app.route("/api/teacher/delete", methods=["POST"])
def teacher_delete():
    """删除教师（软删除）"""
    try:
        data = request.json
        teacher_id = data.get('id')
        db = get_db()
        cur = db.cursor()
        cur.execute("UPDATE teacher SET status='inactive' WHERE id=%s", (teacher_id,))
        db.commit()
        cur.close()
        db.close()
        return jsonify({"code": 200, "msg": "删除成功"})
    except Exception as e:
        return jsonify({"code": 500, "msg": f"删除失败: {str(e)}"}), 500

@app.route("/api/teacher/salary/statistics", methods=["GET"])
def teacher_salary_statistics():
    """教师课酬统计"""
    try:
        teacher_id = request.args.get('teacher_id', type=int)
        month = request.args.get('month')
        
        db = get_db()
        cur = db.cursor()
        
        sql = """
            SELECT 
                t.id as teacher_id,
                t.name as teacher_name,
                t.subject,
                t.class_fee,
                COUNT(CASE WHEN s.status = 'completed' THEN 1 END) as completed_classes,
                COALESCE(SUM(CASE WHEN s.status = 'completed' THEN s.duration ELSE 0 END), 0) as total_hours,
                COALESCE(SUM(CASE WHEN s.status = 'completed' THEN s.duration * t.class_fee ELSE 0 END), 0) as total_amount
            FROM teacher t
            LEFT JOIN course_schedule s ON t.id = s.teacher_id
            WHERE t.status = 'active'
        """
        params = []
        
        if teacher_id:
            sql += " AND t.id = %s"
            params.append(teacher_id)
        if month:
            sql += " AND TO_CHAR(s.class_date, 'YYYY-MM') = %s"
            params.append(month)
        
        sql += " GROUP BY t.id, t.name, t.subject, t.class_fee ORDER BY t.id"
        
        cur.execute(sql, params)
        data = cur.fetchall()
        cur.close()
        db.close()
        
        result = []
        for r in data:
            result.append({
                "teacher_id": r[0],
                "teacher_name": r[1] or '',
                "subject": r[2] or '',
                "class_fee": float(r[3]) if r[3] else 0,
                "completed_classes": r[4] or 0,
                "total_hours": float(r[5]) if r[5] else 0,
                "total_amount": float(r[6]) if r[6] else 0
            })
        
        return jsonify({"code": 200, "data": result})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500

# ==================== 课时管理模块 ====================
@app.route("/api/course/list")
def course_list():
    """获取课时包列表"""
    try:
        db = get_db()
        cur = db.cursor()
        cur.execute("""
            SELECT cp.id, s.name, cp.total, cp.used, cp.surplus, cp.course_name 
            FROM course_package cp 
            JOIN student s ON cp.student_id=s.id 
            WHERE cp.status='active'
            ORDER BY cp.id DESC
        """)
        data = cur.fetchall()
        cur.close()
        db.close()
        result = [{"id": r[0], "student_name": r[1], "total": float(r[2]) if r[2] else 0,
                   "used": float(r[3]) if r[3] else 0, "surplus": float(r[4]) if r[4] else 0,
                   "course_name": r[5] or '标准课程'} for r in data]
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
        cur.execute("""
            INSERT INTO course_package (student_id, total, used, surplus, course_name) 
            VALUES (%s, %s, 0, %s, %s)
        """, (d['student_id'], d['total'], d['total'], d.get('course_name', '标准课程')))
        db.commit()
        cur.close()
        db.close()
        return jsonify({"code": 200, "msg": "添加成功"})
    except Exception as e:
        return jsonify({"code": 500, "msg": f"添加失败: {str(e)}"}), 500

# ==================== 排课管理模块 ====================
@app.route("/api/schedule/calendar", methods=["GET"])
def get_schedule_calendar():
    """获取指定日期的课程（供首页使用）"""
    try:
        date_str = request.args.get('date')
        if not date_str:
            return jsonify({"code": 400, "msg": "缺少日期参数"}), 400

        db = get_db()
        cur = db.cursor()
        cur.execute("""
            SELECT 
                s.id,
                s.class_time,
                s.subject,
                s.classroom,
                COALESCE(s.status, 'scheduled') as status,
                t.name as teacher_name
            FROM course_schedule s
            LEFT JOIN teacher t ON s.teacher_id = t.id
            WHERE s.class_date = %s 
              AND (s.status IS NULL OR s.status != 'cancelled')
            ORDER BY s.class_time
        """, (date_str,))
        
        rows = cur.fetchall()
        cur.close()
        db.close()

        schedule_list = []
        for row in rows:
            schedule_list.append({
                "id": row[0],
                "class_time": row[1],
                "subject": row[2],
                "classroom": row[3] or '',
                "status": row[4],
                "teacher_name": row[5] or '待分配'
            })

        return jsonify({"code": 200, "data": schedule_list})
    except Exception as e:
        print(f"获取日历数据错误: {str(e)}")
        return jsonify({"code": 500, "msg": str(e)}), 500

@app.route("/api/schedule/week", methods=["GET"])
def get_week_schedule():
    """获取周课表数据"""
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        if not start_date or not end_date:
            return jsonify({"code": 400, "msg": "缺少日期参数"}), 400
        
        db = get_db()
        cur = db.cursor()
        
        cur.execute("""
            SELECT 
                s.id,
                s.class_date,
                EXTRACT(DOW FROM s.class_date) as weekday,
                s.class_time,
                s.subject,
                s.classroom,
                COALESCE(s.status, 'scheduled') as status,
                COALESCE(s.duration, 2) as duration,
                t.name as teacher_name
            FROM course_schedule s
            LEFT JOIN teacher t ON s.teacher_id = t.id
            WHERE s.class_date BETWEEN %s AND %s
              AND (s.status IS NULL OR s.status != 'cancelled')
            ORDER BY s.class_date, s.class_time
        """, (start_date, end_date))
        
        data = cur.fetchall()
        cur.close()
        db.close()
        
        week_schedule = {}
        for row in data:
            weekday = int(row[2])
            weekday_idx = 6 if weekday == 0 else weekday - 1
            time_slot = row[3]
            
            if weekday_idx not in week_schedule:
                week_schedule[weekday_idx] = {}
            
            week_schedule[weekday_idx][time_slot] = {
                "id": row[0],
                "subject": row[4] or '',
                "teacher": row[8] or '',
                "place": row[5] or '',
                "duration": float(row[7]) if row[7] else 2,
                "status": row[6]
            }
        
        return jsonify({"code": 200, "data": week_schedule})
    except Exception as e:
        print(f"获取周课表错误: {str(e)}")
        return jsonify({"code": 500, "msg": str(e)}), 500

@app.route("/api/schedule/detail/<int:schedule_id>", methods=["GET"])
def schedule_detail(schedule_id):
    """获取排课详情"""
    try:
        db = get_db()
        cur = db.cursor()
        
        cur.execute("""
            SELECT 
                s.id,
                s.student_id,
                s.teacher_id,
                s.class_date,
                s.class_time,
                s.subject,
                s.classroom,
                s.status,
                s.duration,
                t.name as teacher_name
            FROM course_schedule s
            LEFT JOIN teacher t ON s.teacher_id = t.id
            WHERE s.id = %s
        """, (schedule_id,))
        
        data = cur.fetchone()
        cur.close()
        db.close()
        
        if data:
            return jsonify({
                "code": 200,
                "data": {
                    "id": data[0],
                    "student_id": data[1],
                    "teacher_id": data[2],
                    "class_date": str(data[3]),
                    "class_time": data[4],
                    "subject": data[5],
                    "classroom": data[6] or '',
                    "status": data[7] or 'scheduled',
                    "duration": float(data[8]) if data[8] else 2,
                    "teacher_name": data[9] or ''
                }
            })
        return jsonify({"code": 404, "msg": "排课不存在"})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500

@app.route("/api/schedule/check", methods=["POST"])
def schedule_check():
    """检查排课冲突"""
    try:
        data = request.json
        teacher_id = data.get('teacher_id')
        classroom = data.get('classroom')
        class_date = data.get('class_date')
        class_time = data.get('class_time')
        schedule_id = data.get('id', 0)
        
        db = get_db()
        cur = db.cursor()
        
        # 检查教师时间冲突
        teacher_conflict = False
        if teacher_id:
            cur.execute("""
                SELECT COUNT(*) FROM course_schedule
                WHERE teacher_id = %s 
                AND class_date = %s 
                AND class_time = %s 
                AND id != %s
                AND (status IS NULL OR status != 'cancelled')
            """, (teacher_id, class_date, class_time, schedule_id))
            teacher_conflict = cur.fetchone()[0] > 0
        
        # 检查教室冲突
        room_conflict = False
        if classroom:
            cur.execute("""
                SELECT COUNT(*) FROM course_schedule
                WHERE classroom = %s 
                AND class_date = %s 
                AND class_time = %s 
                AND id != %s
                AND (status IS NULL OR status != 'cancelled')
            """, (classroom, class_date, class_time, schedule_id))
            room_conflict = cur.fetchone()[0] > 0
        
        cur.close()
        db.close()
        
        return jsonify({
            "code": 200,
            "has_conflict": teacher_conflict or room_conflict,
            "teacher_conflict": teacher_conflict,
            "room_conflict": room_conflict
        })
    except Exception as e:
        print(f"冲突检测错误: {str(e)}")
        return jsonify({"code": 500, "msg": str(e)}), 500

@app.route("/api/schedule/save", methods=["POST"])
def schedule_save():
    """保存排课"""
    try:
        data = request.json
        print("=== 收到保存请求 ===")
        print(json.dumps(data, ensure_ascii=False))
        
        # 提取数据
        subject = data.get('subject')
        teacher_id = data.get('teacher_id')
        student_id = data.get('student_id')
        classroom = data.get('classroom')
        class_date = data.get('date')
        class_time = data.get('time')
        duration = data.get('duration', 2)
        
        # 验证必填字段
        if not subject:
            return jsonify({"code": 400, "msg": "课程名称不能为空"}), 400
        if not class_date:
            return jsonify({"code": 400, "msg": "日期不能为空"}), 400
        if not class_time:
            return jsonify({"code": 400, "msg": "时间不能为空"}), 400
        
        # 处理 teacher_id
        if teacher_id:
            try:
                teacher_id = int(teacher_id)
            except (ValueError, TypeError):
                teacher_id = None
        
        db = get_db()
        cur = db.cursor()
        
        # 检查是否已存在
        cur.execute("""
            SELECT id FROM course_schedule
            WHERE class_date = %s AND class_time = %s
            AND (status IS NULL OR status != 'cancelled')
        """, (class_date, class_time))
        
        existing = cur.fetchone()
        
        if existing:
            cur.execute("""
                UPDATE course_schedule 
                SET subject = %s, teacher_id = %s, student_id = %s, classroom = %s, duration = %s, status = 'scheduled'
                WHERE id = %s
            """, (subject, teacher_id, student_id, classroom, duration, existing[0]))
            msg = "更新成功"
        else:
            cur.execute("""
                INSERT INTO course_schedule 
                (subject, teacher_id, student_id, classroom, class_date, class_time, duration, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'scheduled')
                RETURNING id
            """, (subject, teacher_id, student_id, classroom, class_date, class_time, duration))
            new_id = cur.fetchone()[0]
            msg = f"添加成功，ID: {new_id}"
        
        db.commit()
        print(f"数据库操作成功: {msg}")
        
        cur.close()
        db.close()
        
        return jsonify({"code": 200, "msg": msg})
    except Exception as e:
        print(f"保存排课错误: {str(e)}")
        traceback.print_exc()
        return jsonify({"code": 500, "msg": str(e)}), 500

@app.route("/api/schedule/update", methods=["POST"])
def schedule_update():
    """更新排课信息"""
    try:
        data = request.json
        print("=== 收到更新请求 ===")
        print(json.dumps(data, ensure_ascii=False))
        
        schedule_id = data.get('id')
        subject = data.get('subject')
        teacher_id = data.get('teacher_id')
        student_id = data.get('student_id')
        classroom = data.get('classroom')
        class_date = data.get('date')
        class_time = data.get('time')
        duration = data.get('duration', 2)
        
        if not schedule_id:
            return jsonify({"code": 400, "msg": "缺少课程ID"}), 400
        
        # 处理 teacher_id
        if teacher_id:
            try:
                teacher_id = int(teacher_id)
            except (ValueError, TypeError):
                teacher_id = None
        
        db = get_db()
        cur = db.cursor()
        
        cur.execute("""
            UPDATE course_schedule 
            SET subject = %s, teacher_id = %s, student_id = %s, 
                classroom = %s, class_date = %s, class_time = %s, duration = %s
            WHERE id = %s
        """, (subject, teacher_id, student_id, classroom, class_date, class_time, duration, schedule_id))
        
        db.commit()
        print(f"更新成功，ID: {schedule_id}")
        
        cur.close()
        db.close()
        
        return jsonify({"code": 200, "msg": "更新成功"})
    except Exception as e:
        print(f"更新排课错误: {str(e)}")
        return jsonify({"code": 500, "msg": str(e)}), 500

@app.route("/api/schedule/delete", methods=["POST"])
def schedule_delete():
    """删除排课（软删除）"""
    try:
        data = request.json
        schedule_id = data.get('id')
        
        db = get_db()
        cur = db.cursor()
        
        cur.execute("UPDATE course_schedule SET status = 'cancelled' WHERE id = %s", (schedule_id,))
        
        db.commit()
        cur.close()
        db.close()
        
        return jsonify({"code": 200, "msg": "取消成功"})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500

@app.route("/api/schedule/clear_week", methods=["POST"])
def clear_week_schedule():
    """清空一周的课程"""
    try:
        data = request.json
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        
        db = get_db()
        cur = db.cursor()
        
        cur.execute("""
            UPDATE course_schedule 
            SET status = 'cancelled' 
            WHERE class_date BETWEEN %s AND %s
        """, (start_date, end_date))
        
        db.commit()
        cur.close()
        db.close()
        
        return jsonify({"code": 200, "msg": "清空成功"})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500

@app.route("/api/schedule/tomorrow", methods=["GET"])
def schedule_tomorrow():
    """获取明天的课程（用于提醒）"""
    try:
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        
        db = get_db()
        cur = db.cursor()
        cur.execute("""
            SELECT 
                s.subject,
                s.class_time,
                s.classroom,
                t.name as teacher_name,
                stu.name as student_name
            FROM course_schedule s
            LEFT JOIN teacher t ON s.teacher_id = t.id
            LEFT JOIN student stu ON s.student_id = stu.id
            WHERE s.class_date = %s 
              AND (s.status IS NULL OR s.status != 'cancelled')
            ORDER BY s.class_time
        """, (tomorrow,))
        
        data = cur.fetchall()
        cur.close()
        db.close()
        
        result = []
        for row in data:
            result.append({
                "subject": row[0],
                "class_time": row[1],
                "classroom": row[2] or '',
                "teacher_name": row[3] or '待分配',
                "student_name": row[4] or '集体课'
            })
        
        return jsonify({"code": 200, "data": result, "date": tomorrow})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500

# ==================== 仪表盘数据 ====================
@app.route("/api/dashboard/stats")
def dashboard_stats():
    """获取首页统计数据"""
    try:
        db = get_db()
        cur = db.cursor()

        # 学生总数
        try:
            cur.execute("SELECT COUNT(*) FROM student WHERE status = 1")
            student_count = cur.fetchone()[0]
        except Exception:
            cur.execute("SELECT COUNT(*) FROM student")
            student_count = cur.fetchone()[0]

        # 教师总数
        try:
            cur.execute("SELECT COUNT(*) FROM teacher WHERE status = 'active'")
            teacher_count = cur.fetchone()[0]
        except Exception:
            cur.execute("SELECT COUNT(*) FROM teacher")
            teacher_count = cur.fetchone()[0]

        # 今日课程
        today = datetime.now().strftime("%Y-%m-%d")
        cur.execute("""
            SELECT COUNT(*) FROM course_schedule 
            WHERE class_date = %s 
              AND (status IS NULL OR status != 'cancelled')
        """, (today,))
        today_classes = cur.fetchone()[0]

        # 剩余总课时
        cur.execute("SELECT COALESCE(SUM(surplus), 0) FROM course_package WHERE status='active'")
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
        print(f"仪表盘错误: {str(e)}")
        return jsonify({"code": 500, "msg": str(e)}), 500

# ==================== 搜索接口 ====================
@app.route("/api/search/teachers", methods=["GET"])
def search_teachers():
    """模糊查询教师"""
    try:
        keyword = request.args.get('keyword', '')
        db = get_db()
        cur = db.cursor()
        cur.execute("""
            SELECT id, name, phone, subject 
            FROM teacher 
            WHERE status = 'active' 
            AND (name LIKE %s OR subject LIKE %s)
            LIMIT 20
        """, (f'%{keyword}%', f'%{keyword}%'))
        data = cur.fetchall()
        cur.close()
        db.close()
        
        result = [{"id": r[0], "name": r[1], "phone": r[2], "subject": r[3]} for r in data]
        return jsonify({"code": 200, "data": result})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500

@app.route("/api/search/students", methods=["GET"])
def search_students():
    """模糊查询学生"""
    try:
        keyword = request.args.get('keyword', '')
        db = get_db()
        cur = db.cursor()
        cur.execute("""
            SELECT id, name, phone, grade 
            FROM student 
            WHERE status = 1 
            AND (name LIKE %s OR phone LIKE %s)
            LIMIT 30
        """, (f'%{keyword}%', f'%{keyword}%'))
        data = cur.fetchall()
        cur.close()
        db.close()
        
        result = [{"id": r[0], "name": r[1], "phone": r[2], "grade": r[3]} for r in data]
        return jsonify({"code": 200, "data": result})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500

@app.route("/api/courses/list", methods=["GET"])
def get_courses_list():
    """获取课程列表"""
    try:
        db = get_db()
        cur = db.cursor()
        cur.execute("""
            SELECT DISTINCT subject FROM course_schedule WHERE subject IS NOT NULL AND subject != ''
            UNION
            SELECT '数学' as subject
            UNION
            SELECT '语文'
            UNION
            SELECT '英语'
            UNION
            SELECT '物理'
            UNION
            SELECT '化学'
            ORDER BY subject
        """)
        data = cur.fetchall()
        cur.close()
        db.close()
        
        result = [r[0] for r in data if r[0]]
        return jsonify({"code": 200, "data": result})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500

@app.route("/api/rooms/list", methods=["GET"])
def get_rooms_list():
    """获取教室列表"""
    try:
        db = get_db()
        cur = db.cursor()
        cur.execute("""
            SELECT DISTINCT classroom FROM course_schedule WHERE classroom IS NOT NULL AND classroom != ''
            UNION
            SELECT 'A101' as classroom
            UNION
            SELECT 'A102'
            UNION
            SELECT 'A103'
            UNION
            SELECT 'B201'
            UNION
            SELECT 'B202'
            UNION
            SELECT 'C301'
            ORDER BY classroom
        """)
        data = cur.fetchall()
        cur.close()
        db.close()
        
        result = [r[0] for r in data if r[0]]
        return jsonify({"code": 200, "data": result})
    except Exception as e:
        return jsonify({"code": 200, "data": ['A101', 'A102', 'A103', 'B201', 'B202', 'C301']}), 200

# ------------------- 启动应用 -------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
