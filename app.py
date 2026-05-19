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
    """获取周课表数据 - 每个时间段支持多课程"""
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
                t.name as teacher_name,
                s.student_ids,
                s.created_at
            FROM course_schedule s
            LEFT JOIN teacher t ON s.teacher_id = t.id
            WHERE s.class_date BETWEEN %s AND %s
              AND (s.status IS NULL OR s.status != 'cancelled')
            ORDER BY s.class_date, s.class_time, s.created_at
        """, (start_date, end_date))
        
        data = cur.fetchall()
        
        # 获取学生名称映射
        cur.execute("SELECT id, name FROM student")
        students_map = {row[0]: row[1] for row in cur.fetchall()}
        
        cur.close()
        db.close()
        
        # 构建数据结构：week_schedule[weekday][time_slot] = [课程列表]
        week_schedule = {}
        for row in data:
            weekday = int(row[2])
            # 转换：数据库周日=0，我们改为周一=0，周日=6
            weekday_idx = 6 if weekday == 0 else weekday - 1
            time_slot = row[3]
            
            if weekday_idx not in week_schedule:
                week_schedule[weekday_idx] = {}
            
            if time_slot not in week_schedule[weekday_idx]:
                week_schedule[weekday_idx][time_slot] = []
            
            # 解析学生ID列表，获取学生名称
            student_names = []
            student_id_list = []
            if row[9]:  # student_ids
                student_id_list = [int(x) for x in row[9].split(',') if x]
                student_names = [students_map.get(sid, '') for sid in student_id_list if students_map.get(sid)]
            
            week_schedule[weekday_idx][time_slot].append({
                "id": row[0],
                "subject": row[4] or '',
                "teacher": row[8] or '',
                "place": row[5] or '',
                "duration": float(row[7]) if row[7] else 2,
                "status": row[6],
                "student_ids": student_id_list,
                "students": student_names
            })
        
        return jsonify({"code": 200, "data": week_schedule})
    except Exception as e:
        print(f"获取周课表错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"code": 500, "msg": str(e)}), 500

@app.route("/api/schedule/detail/<int:schedule_id>", methods=["GET"])
def schedule_detail(schedule_id):
    """获取排课详情"""
    try:
        db = get_db()
        cur = db.cursor()
        
        # 先检查表结构
        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'course_schedule' AND column_name = 'student_ids'
        """)
        has_student_ids = cur.fetchone() is not None
        
        if has_student_ids:
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
                    COALESCE(s.student_ids, '') as student_ids,
                    t.name as teacher_name
                FROM course_schedule s
                LEFT JOIN teacher t ON s.teacher_id = t.id
                WHERE s.id = %s
            """, (schedule_id,))
        else:
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
                    '' as student_ids,
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
                    "student_ids": data[9] or '',
                    "teacher_name": data[10] or ''
                }
            })
        return jsonify({"code": 404, "msg": "排课不存在"})
    except Exception as e:
        print(f"获取排课详情错误: {str(e)}")
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
    """保存排课 - 支持多学生"""
    try:
        data = request.json
        print("=== 收到保存请求 ===")
        print(json.dumps(data, ensure_ascii=False))
        
        # 提取数据
        subject = data.get('subject')
        teacher_id = data.get('teacher_id')
        student_ids = data.get('student_ids', '')  # 获取学生ID字符串
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
        
        # 确保 student_ids 是字符串
        if student_ids is None:
            student_ids = ''
        elif isinstance(student_ids, list):
            student_ids = ','.join(map(str, student_ids))
        
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
                SET subject = %s, teacher_id = %s, student_ids = %s, classroom = %s, duration = %s, status = 'scheduled'
                WHERE id = %s
            """, (subject, teacher_id, student_ids, classroom, duration, existing[0]))
            msg = "更新成功"
        else:
            cur.execute("""
                INSERT INTO course_schedule 
                (subject, teacher_id, student_ids, classroom, class_date, class_time, duration, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'scheduled')
                RETURNING id
            """, (subject, teacher_id, student_ids, classroom, class_date, class_time, duration))
            new_id = cur.fetchone()[0]
            msg = f"添加成功，ID: {new_id}"
        
        db.commit()
        print(f"数据库操作成功: {msg}, student_ids: {student_ids}")
        
        cur.close()
        db.close()
        
        return jsonify({"code": 200, "msg": msg})
    except Exception as e:
        print(f"保存排课错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"code": 500, "msg": str(e)}), 500


@app.route("/api/schedule/update", methods=["POST"])
def schedule_update():
    """更新排课信息 - 支持多学生"""
    try:
        data = request.json
        print("=== 收到更新请求 ===")
        print(json.dumps(data, ensure_ascii=False))
        
        schedule_id = data.get('id')
        subject = data.get('subject')
        teacher_id = data.get('teacher_id')
        student_ids = data.get('student_ids')  # 新增：获取学生ID字符串
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
        
        # 处理 student_ids
        if student_ids and isinstance(student_ids, list):
            student_ids = ','.join(map(str, student_ids))
        
        db = get_db()
        cur = db.cursor()
        
        cur.execute("""
            UPDATE course_schedule 
            SET subject = %s, teacher_id = %s, student_ids = %s, 
                classroom = %s, class_date = %s, class_time = %s, duration = %s
            WHERE id = %s
        """, (subject, teacher_id, student_ids, classroom, class_date, class_time, duration, schedule_id))
        
        db.commit()
        print(f"更新成功，ID: {schedule_id}, student_ids: {student_ids}")
        
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

@app.route("/api/schedule/copy_week", methods=["POST"])
def copy_week_schedule():
    """复制本周课程到下一周"""
    try:
        data = request.json
        current_start_date = data.get('current_start_date')
        current_end_date = data.get('current_end_date')
        
        if not current_start_date or not current_end_date:
            return jsonify({"code": 400, "msg": "缺少日期参数"}), 400
        
        db = get_db()
        cur = db.cursor()
        
        # 获取本周的所有课程（包含具体日期）
        cur.execute("""
            SELECT 
                id,
                student_id,
                teacher_id,
                subject,
                classroom,
                class_date,
                class_time,
                duration,
                student_ids,
                repeat_type,
                status
            FROM course_schedule
            WHERE class_date BETWEEN %s AND %s
              AND (status IS NULL OR status != 'cancelled')
        """, (current_start_date, current_end_date))
        
        courses = cur.fetchall()
        
        if not courses:
            cur.close()
            db.close()
            return jsonify({"code": 200, "msg": "本周没有课程可复制", "count": 0})
        
        print(f"找到 {len(courses)} 门课程待复制")
        
        # 计算下一周的日期范围
        from datetime import datetime, timedelta
        
        current_start = datetime.strptime(current_start_date, "%Y-%m-%d")
        next_start = current_start + timedelta(days=7)
        next_end = next_start + timedelta(days=6)
        
        print(f"当前周: {current_start_date} ~ {current_end_date}")
        print(f"下一周: {next_start.strftime('%Y-%m-%d')} ~ {next_end.strftime('%Y-%m-%d')}")
        
        # 先查询下一周已有哪些课程（用于冲突检测）
        cur.execute("""
            SELECT class_date, class_time, id
            FROM course_schedule
            WHERE class_date BETWEEN %s AND %s
              AND (status IS NULL OR status != 'cancelled')
        """, (next_start.strftime("%Y-%m-%d"), next_end.strftime("%Y-%m-%d")))
        
        existing_courses = cur.fetchall()
        existing_map = {}
        for ec in existing_courses:
            key = f"{ec[0]}_{ec[1]}"
            existing_map[key] = ec[2]
        
        print(f"下一周已有 {len(existing_courses)} 门课程")
        
        copied_count = 0
        skipped_count = 0
        
        for course in courses:
            course_id = course[0]
            student_id = course[1]
            teacher_id = course[2]
            subject = course[3]
            classroom = course[4]
            old_date = course[5]
            class_time = course[6]
            duration = course[7]
            student_ids = course[8]
            
            # 计算新日期：原日期 + 7天
            # 注意：old_date 已经是 datetime.date 对象，需要转换为 datetime
            if isinstance(old_date, str):
                old_date = datetime.strptime(str(old_date), "%Y-%m-%d")
            elif hasattr(old_date, 'strftime'):
                # 已经是 datetime 对象
                pass
            else:
                old_date = datetime.strptime(str(old_date), "%Y-%m-%d")
            
            new_date = old_date + timedelta(days=7)
            new_date_str = new_date.strftime("%Y-%m-%d")
            
            print(f"处理课程: {subject}, 原日期: {old_date.strftime('%Y-%m-%d')}, 新日期: {new_date_str}")
            
            # 检查下一周是否已经有相同时间段的课程
            key = f"{new_date_str}_{class_time}"
            
            if key in existing_map:
                print(f"  跳过: 下一周 {new_date_str} {class_time} 已有课程 (ID: {existing_map[key]})")
                skipped_count += 1
                continue
            
            # 插入新课程
            cur.execute("""
                INSERT INTO course_schedule 
                (student_id, teacher_id, subject, classroom, class_date, class_time, duration, student_ids, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'scheduled')
                RETURNING id
            """, (student_id, teacher_id, subject, classroom, new_date_str, class_time, duration, student_ids))
            
            new_id = cur.fetchone()[0]
            print(f"  成功复制: ID {course_id} -> {new_id}, 日期: {new_date_str}")
            copied_count += 1
        
        db.commit()
        cur.close()
        db.close()
        
        print(f"复制完成: 成功 {copied_count} 门, 跳过 {skipped_count} 门")
        
        if copied_count == 0:
            msg = "没有课程可复制到下一周"
        else:
            msg = f"成功复制 {copied_count} 门课程到下一周"
            if skipped_count > 0:
                msg += f"，跳过 {skipped_count} 门（下一周已有相同时间段课程）"
        
        return jsonify({
            "code": 200, 
            "msg": msg,
            "count": copied_count,
            "skipped": skipped_count,
            "next_week_start": next_start.strftime("%Y-%m-%d"),
            "next_week_end": next_end.strftime("%Y-%m-%d")
        })
    except Exception as e:
        print(f"复制周课表错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"code": 500, "msg": str(e)}), 500

# ==================== 学生课时管理模块 ====================

@app.route("/api/student/hours/list", methods=["GET"])
def student_hours_list():
    """获取学生课时列表"""
    try:
        student_id = request.args.get('student_id', type=int)
        
        db = get_db()
        cur = db.cursor()
        
        sql = """
            SELECT 
                cp.id,
                s.id as student_id,
                s.name as student_name,
                s.grade,
                s.phone,
                cp.course_name,
                cp.total,
                cp.used,
                cp.surplus,
                cp.expire_date,
                cp.status,
                cp.created_at
            FROM course_package cp
            JOIN student s ON cp.student_id = s.id
            WHERE cp.status = 'active'
        """
        params = []
        
        if student_id:
            sql += " AND cp.student_id = %s"
            params.append(student_id)
        
        sql += " ORDER BY cp.created_at DESC"
        
        cur.execute(sql, params)
        data = cur.fetchall()
        cur.close()
        db.close()
        
        result = []
        for r in data:
            result.append({
                "id": r[0],
                "student_id": r[1],
                "student_name": r[2],
                "grade": r[3] or '',
                "phone": r[4] or '',
                "course_name": r[5] or '标准课程',
                "total": float(r[6]) if r[6] else 0,
                "used": float(r[7]) if r[7] else 0,
                "surplus": float(r[8]) if r[8] else 0,
                "expire_date": str(r[9]) if r[9] else None,
                "status": r[10] or 'active',
                "created_at": str(r[11]) if r[11] else None
            })
        
        return jsonify({"code": 200, "data": result})
    except Exception as e:
        print(f"获取课时列表错误: {str(e)}")
        return jsonify({"code": 500, "msg": str(e)}), 500


@app.route("/api/student/hours/statistics", methods=["GET"])
def student_hours_statistics():
    """获取课时统计数据"""
    try:
        db = get_db()
        cur = db.cursor()
        
        # 总课时统计
        cur.execute("""
            SELECT 
                COUNT(*) as total_packages,
                COALESCE(SUM(total), 0) as total_hours,
                COALESCE(SUM(used), 0) as used_hours,
                COALESCE(SUM(surplus), 0) as surplus_hours
            FROM course_package
            WHERE status = 'active'
        """)
        total_stats = cur.fetchone()
        
        # 按年级统计
        cur.execute("""
            SELECT 
                s.grade,
                COUNT(cp.id) as package_count,
                COALESCE(SUM(cp.surplus), 0) as surplus_hours
            FROM course_package cp
            JOIN student s ON cp.student_id = s.id
            WHERE cp.status = 'active'
            GROUP BY s.grade
            ORDER BY s.grade
        """)
        grade_stats = cur.fetchall()
        
        # 即将过期课时（30天内）
        cur.execute("""
            SELECT 
                COUNT(*) as expiring_count,
                COALESCE(SUM(surplus), 0) as expiring_hours
            FROM course_package
            WHERE status = 'active'
              AND expire_date IS NOT NULL
              AND expire_date <= CURRENT_DATE + INTERVAL '30 days'
        """)
        expiring_stats = cur.fetchone()
        
        cur.close()
        db.close()
        
        grade_list = []
        for g in grade_stats:
            grade_list.append({
                "grade": g[0] or '未设置',
                "package_count": g[1],
                "surplus_hours": float(g[2]) if g[2] else 0
            })
        
        return jsonify({"code": 200, "data": {
            "total_packages": total_stats[0] or 0,
            "total_hours": float(total_stats[1]) if total_stats[1] else 0,
            "used_hours": float(total_stats[2]) if total_stats[2] else 0,
            "surplus_hours": float(total_stats[3]) if total_stats[3] else 0,
            "grade_stats": grade_list,
            "expiring_count": expiring_stats[0] or 0,
            "expiring_hours": float(expiring_stats[1]) if expiring_stats[1] else 0
        }})
    except Exception as e:
        print(f"获取课时统计错误: {str(e)}")
        return jsonify({"code": 500, "msg": str(e)}), 500


@app.route("/api/student/hours/add", methods=["POST"])
def student_hours_add():
    """添加课时包"""
    try:
        data = request.json
        student_id = data.get('student_id')
        total_hours = data.get('total_hours')
        course_name = data.get('course_name', '标准课程')
        expire_date = data.get('expire_date')
        
        if not student_id:
            return jsonify({"code": 400, "msg": "请选择学生"}), 400
        if not total_hours or total_hours <= 0:
            return jsonify({"code": 400, "msg": "请输入有效的课时数"}), 400
        
        db = get_db()
        cur = db.cursor()
        
        cur.execute("""
            INSERT INTO course_package (student_id, total, used, surplus, course_name, expire_date, status)
            VALUES (%s, %s, 0, %s, %s, %s, 'active')
            RETURNING id
        """, (student_id, total_hours, total_hours, course_name, expire_date))
        
        new_id = cur.fetchone()[0]
        db.commit()
        cur.close()
        db.close()
        
        return jsonify({"code": 200, "msg": "添加成功", "data": {"id": new_id}})
    except Exception as e:
        print(f"添加课时包错误: {str(e)}")
        return jsonify({"code": 500, "msg": str(e)}), 500


@app.route("/api/student/hours/consume", methods=["POST"])
def student_hours_consume():
    """消耗课时"""
    try:
        data = request.json
        package_id = data.get('package_id')
        hours = data.get('hours')
        schedule_id = data.get('schedule_id')
        note = data.get('note', '')
        
        if not package_id:
            return jsonify({"code": 400, "msg": "请选择课时包"}), 400
        if not hours or hours <= 0:
            return jsonify({"code": 400, "msg": "请输入有效的消耗课时"}), 400
        
        db = get_db()
        cur = db.cursor()
        
        # 检查剩余课时
        cur.execute("SELECT surplus FROM course_package WHERE id = %s", (package_id,))
        result = cur.fetchone()
        if not result:
            return jsonify({"code": 404, "msg": "课时包不存在"}), 404
        
        surplus = result[0]
        if surplus < hours:
            return jsonify({"code": 400, "msg": f"剩余课时不足，仅剩 {surplus} 小时"}), 400
        
        # 更新课时包
        cur.execute("""
            UPDATE course_package 
            SET used = used + %s, surplus = surplus - %s
            WHERE id = %s
        """, (hours, hours, package_id))
        
        # 记录消耗日志
        cur.execute("""
            INSERT INTO hour_consumption (package_id, schedule_id, hours, consume_date, note)
            VALUES (%s, %s, %s, CURRENT_DATE, %s)
        """, (package_id, schedule_id, hours, note))
        
        db.commit()
        cur.close()
        db.close()
        
        return jsonify({"code": 200, "msg": f"成功消耗 {hours} 课时"})
    except Exception as e:
        print(f"消耗课时错误: {str(e)}")
        return jsonify({"code": 500, "msg": str(e)}), 500


@app.route("/api/student/hours/consumption/list", methods=["GET"])
def hours_consumption_list():
    """获取课时消耗记录"""
    try:
        student_id = request.args.get('student_id', type=int)
        package_id = request.args.get('package_id', type=int)
        
        db = get_db()
        cur = db.cursor()
        
        sql = """
            SELECT 
                hc.id,
                hc.package_id,
                cp.course_name,
                s.name as student_name,
                hc.hours,
                hc.consume_date,
                hc.note,
                hc.created_at,
                cs.subject as schedule_subject,
                cs.class_date
            FROM hour_consumption hc
            LEFT JOIN course_package cp ON hc.package_id = cp.id
            LEFT JOIN student s ON cp.student_id = s.id
            LEFT JOIN course_schedule cs ON hc.schedule_id = cs.id
            WHERE 1=1
        """
        params = []
        
        if student_id:
            sql += " AND cp.student_id = %s"
            params.append(student_id)
        if package_id:
            sql += " AND hc.package_id = %s"
            params.append(package_id)
        
        sql += " ORDER BY hc.consume_date DESC, hc.created_at DESC LIMIT 100"
        
        cur.execute(sql, params)
        data = cur.fetchall()
        cur.close()
        db.close()
        
        result = []
        for r in data:
            result.append({
                "id": r[0],
                "package_id": r[1],
                "course_name": r[2] or '',
                "student_name": r[3] or '',
                "hours": float(r[4]) if r[4] else 0,
                "consume_date": str(r[5]) if r[5] else None,
                "note": r[6] or '',
                "schedule_subject": r[8] or '',
                "class_date": str(r[9]) if r[9] else None
            })
        
        return jsonify({"code": 200, "data": result})
    except Exception as e:
        print(f"获取消耗记录错误: {str(e)}")
        return jsonify({"code": 500, "msg": str(e)}), 500


@app.route("/api/student/hours/export", methods=["POST"])
def student_hours_export():
    """导出课时报表"""
    try:
        data = request.json
        student_ids = data.get('student_ids', [])
        
        db = get_db()
        cur = db.cursor()
        
        if student_ids:
            placeholders = ','.join(['%s'] * len(student_ids))
            sql = f"""
                SELECT 
                    s.name,
                    s.grade,
                    s.phone,
                    cp.course_name,
                    cp.total,
                    cp.used,
                    cp.surplus,
                    cp.expire_date
                FROM course_package cp
                JOIN student s ON cp.student_id = s.id
                WHERE cp.status = 'active' AND s.id IN ({placeholders})
                ORDER BY s.grade, s.name
            """
            cur.execute(sql, student_ids)
        else:
            cur.execute("""
                SELECT 
                    s.name,
                    s.grade,
                    s.phone,
                    cp.course_name,
                    cp.total,
                    cp.used,
                    cp.surplus,
                    cp.expire_date
                FROM course_package cp
                JOIN student s ON cp.student_id = s.id
                WHERE cp.status = 'active'
                ORDER BY s.grade, s.name
            """)
        
        data = cur.fetchall()
        cur.close()
        db.close()
        
        # 创建Excel文件
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "学生课时统计"
        
        # 设置表头
        headers = ["学生姓名", "年级", "联系电话", "课程名称", "总课时", "已用课时", "剩余课时", "有效期"]
        ws.append(headers)
        
        # 设置表头样式
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
            cell.alignment = Alignment(horizontal="center")
        
        # 写入数据
        for row in data:
            ws.append([
                row[0] or '',
                row[1] or '',
                row[2] or '',
                row[3] or '',
                float(row[4]) if row[4] else 0,
                float(row[5]) if row[5] else 0,
                float(row[6]) if row[6] else 0,
                str(row[7]) if row[7] else '永久'
            ])
        
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
            adjusted_width = min(max_length + 2, 25)
            ws.column_dimensions[col_letter].width = adjusted_width
        
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        
        return send_file(
            output, 
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True, 
            download_name=f'学生课时统计_{datetime.now().strftime("%Y%m%d")}.xlsx'
        )
    except Exception as e:
        print(f"导出课时报表错误: {str(e)}")
        return jsonify({"code": 500, "msg": str(e)}), 500


@app.route("/api/dashboard/stats")
def dashboard_stats():
    """获取首页统计数据"""
    try:
        db = get_db()
        cur = db.cursor()

        # 学生总数 - 查询全部
        cur.execute("SELECT COUNT(*) FROM student")
        student_count = cur.fetchone()[0]

        # 教师总数 - 查询全部
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
        cur.execute("SELECT COALESCE(SUM(surplus), 0) FROM course_package")
        total_surplus = cur.fetchone()[0] or 0

        cur.close()
        db.close()

        print(f"仪表盘数据: 学生={student_count}, 教师={teacher_count}, 今日课程={today_classes}, 剩余课时={total_surplus}")

        return jsonify({"code": 200, "data": {
            "student_count": student_count,
            "teacher_count": teacher_count,
            "today_classes": today_classes,
            "total_surplus_hours": float(total_surplus)
        }})
    except Exception as e:
        print(f"仪表盘错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"code": 200, "data": {
            "student_count": 0,
            "teacher_count": 0,
            "today_classes": 0,
            "total_surplus_hours": 0
        }})

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

# ==================== 学期课时统计模块 ====================

# 学期定义
SEMESTERS = {
    'spring': {'name': '春季学期', 'months': [2, 3, 4, 5, 6], 'start_month': 2, 'end_month': 6},
    'summer': {'name': '暑假', 'months': [7, 8], 'start_month': 7, 'end_month': 8},
    'autumn': {'name': '秋季学期', 'months': [9, 10, 11, 12, 1], 'start_month': 9, 'end_month': 1},
    'winter': {'name': '寒假', 'months': [1, 2], 'start_month': 1, 'end_month': 2}
}

def get_semester_by_date(date_obj):
    """根据日期获取所属学期"""
    month = date_obj.month
    year = date_obj.year
    
    # 春季学期 2-6月
    if 2 <= month <= 6:
        return f"{year}年春季学期", f"{year}-spring"
    # 暑假 7-8月
    elif 7 <= month <= 8:
        return f"{year}年暑假", f"{year}-summer"
    # 秋季学期 9-12月
    elif 9 <= month <= 12:
        return f"{year}年秋季学期", f"{year}-autumn"
    # 寒假 1月（属于前一年）
    elif month == 1:
        return f"{year-1}年寒假", f"{year-1}-winter"
    return None, None

def get_semester_range(semester_key):
    """获取学期的日期范围"""
    parts = semester_key.split('-')
    year = int(parts[0])
    semester_type = parts[1]
    
    if semester_type == 'spring':
        start_date = f"{year}-02-01"
        end_date = f"{year}-06-30"
    elif semester_type == 'summer':
        start_date = f"{year}-07-01"
        end_date = f"{year}-08-31"
    elif semester_type == 'autumn':
        start_date = f"{year}-09-01"
        end_date = f"{year+1}-01-31"
    elif semester_type == 'winter':
        start_date = f"{year}-01-01"
        end_date = f"{year}-02-28"
    else:
        return None, None
    
    return start_date, end_date


@app.route("/api/student/semester/list", methods=["GET"])
def get_semester_list():
    """获取可用的学期列表"""
    try:
        current_year = datetime.now().year
        semesters = []
        
        # 生成近3年的学期
        for year in range(current_year - 1, current_year + 2):
            semesters.append({
                "key": f"{year}-spring",
                "name": f"{year}年春季学期",
                "start_date": f"{year}-02-01",
                "end_date": f"{year}-06-30"
            })
            semesters.append({
                "key": f"{year}-summer",
                "name": f"{year}年暑假",
                "start_date": f"{year}-07-01",
                "end_date": f"{year}-08-31"
            })
            semesters.append({
                "key": f"{year}-autumn",
                "name": f"{year}年秋季学期",
                "start_date": f"{year}-09-01",
                "end_date": f"{year+1}-01-31"
            })
            semesters.append({
                "key": f"{year}-winter",
                "name": f"{year}年寒假",
                "start_date": f"{year}-01-01",
                "end_date": f"{year}-02-28"
            })
        
        # 按时间排序
        semesters.sort(key=lambda x: x['start_date'], reverse=True)
        
        return jsonify({"code": 200, "data": semesters})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500


@app.route("/api/student/semester/statistics", methods=["GET"])
def get_semester_statistics():
    """获取学期课时统计"""
    try:
        semester_key = request.args.get('semester_key')
        student_id = request.args.get('student_id', type=int)
        
        if not semester_key:
            return jsonify({"code": 400, "msg": "请选择学期"}), 400
        
        start_date, end_date = get_semester_range(semester_key)
        if not start_date or not end_date:
            return jsonify({"code": 400, "msg": "无效的学期"}), 400
        
        db = get_db()
        cur = db.cursor()
        
        # 构建查询条件
        sql = """
            SELECT 
                s.id as student_id,
                s.name as student_name,
                s.grade,
                s.phone,
                COUNT(DISTINCT cs.id) as class_count,
                COALESCE(SUM(cs.duration), 0) as total_hours,
                COUNT(DISTINCT cs.class_date) as attendance_days,
                GROUP_CONCAT(DISTINCT cs.subject) as subjects
            FROM student s
            LEFT JOIN course_schedule cs ON cs.student_id = s.id
                OR FIND_IN_SET(s.id, cs.student_ids) > 0
            WHERE cs.class_date BETWEEN %s AND %s
              AND cs.status = 'scheduled'
        """
        params = [start_date, end_date]
        
        if student_id:
            sql += " AND s.id = %s"
            params.append(student_id)
        
        sql += " GROUP BY s.id, s.name, s.grade, s.phone ORDER BY s.grade, s.name"
        
        cur.execute(sql, params)
        data = cur.fetchall()
        cur.close()
        db.close()
        
        # 计算总统计
        total_students = len(data)
        total_classes = sum(row[3] for row in data)
        total_hours = sum(row[4] for row in data)
        
        result = []
        for row in data:
            # 每门课2课时，所以总课时 = 上课次数 × 2
            class_count = row[3] or 0
            calculated_hours = class_count * 2
            
            result.append({
                "student_id": row[0],
                "student_name": row[1] or '',
                "grade": row[2] or '',
                "phone": row[3] or '',
                "class_count": class_count,
                "total_hours": calculated_hours,
                "attendance_days": row[5] or 0,
                "subjects": row[6].split(',') if row[6] else []
            })
        
        return jsonify({
            "code": 200,
            "data": {
                "semester_name": semester_key,
                "start_date": start_date,
                "end_date": end_date,
                "total_students": total_students,
                "total_classes": total_classes,
                "total_hours": total_hours,
                "student_stats": result
            }
        })
    except Exception as e:
        print(f"获取学期统计错误: {str(e)}")
        return jsonify({"code": 500, "msg": str(e)}), 500


@app.route("/api/student/semester/detail", methods=["GET"])
def get_student_semester_detail():
    """获取单个学生的学期课时明细"""
    try:
        student_id = request.args.get('student_id', type=int)
        semester_key = request.args.get('semester_key')
        
        if not student_id:
            return jsonify({"code": 400, "msg": "请选择学生"}), 400
        if not semester_key:
            return jsonify({"code": 400, "msg": "请选择学期"}), 400
        
        start_date, end_date = get_semester_range(semester_key)
        if not start_date or not end_date:
            return jsonify({"code": 400, "msg": "无效的学期"}), 400
        
        db = get_db()
        cur = db.cursor()
        
        # 获取学生信息
        cur.execute("SELECT id, name, grade, phone FROM student WHERE id = %s", (student_id,))
        student = cur.fetchone()
        if not student:
            return jsonify({"code": 404, "msg": "学生不存在"}), 404
        
        # 获取该学生在学期内的课程明细
        cur.execute("""
            SELECT 
                cs.id,
                cs.class_date,
                cs.class_time,
                cs.subject,
                cs.classroom,
                t.name as teacher_name,
                cs.duration
            FROM course_schedule cs
            LEFT JOIN teacher t ON cs.teacher_id = t.id
            WHERE (cs.student_id = %s OR FIND_IN_SET(%s, cs.student_ids) > 0)
              AND cs.class_date BETWEEN %s AND %s
              AND cs.status = 'scheduled'
            ORDER BY cs.class_date, cs.class_time
        """, (student_id, student_id, start_date, end_date))
        
        courses = cur.fetchall()
        cur.close()
        db.close()
        
        # 按日期分组
        schedule_by_date = {}
        for course in courses:
            date_str = str(course[1])
            if date_str not in schedule_by_date:
                schedule_by_date[date_str] = []
            schedule_by_date[date_str].append({
                "id": course[0],
                "class_time": course[2],
                "subject": course[3] or '',
                "classroom": course[4] or '',
                "teacher_name": course[5] or '',
                "duration": float(course[6]) if course[6] else 2
            })
        
        # 转换为列表
        schedule_list = []
        for date, items in sorted(schedule_by_date.items()):
            schedule_list.append({
                "date": date,
                "courses": items,
                "daily_hours": len(items) * 2
            })
        
        total_classes = len(courses)
        total_hours = total_classes * 2
        
        return jsonify({
            "code": 200,
            "data": {
                "student_id": student[0],
                "student_name": student[1] or '',
                "grade": student[2] or '',
                "phone": student[3] or '',
                "semester_name": semester_key,
                "start_date": start_date,
                "end_date": end_date,
                "total_classes": total_classes,
                "total_hours": total_hours,
                "schedule_list": schedule_list
            }
        })
    except Exception as e:
        print(f"获取学生学期明细错误: {str(e)}")
        return jsonify({"code": 500, "msg": str(e)}), 500


@app.route("/api/student/semester/export", methods=["POST"])
def export_semester_report():
    """导出学期课时报表"""
    try:
        data = request.json
        semester_key = data.get('semester_key')
        student_ids = data.get('student_ids', [])
        
        if not semester_key:
            return jsonify({"code": 400, "msg": "请选择学期"}), 400
        
        start_date, end_date = get_semester_range(semester_key)
        if not start_date or not end_date:
            return jsonify({"code": 400, "msg": "无效的学期"}), 400
        
        db = get_db()
        cur = db.cursor()
        
        # 构建查询
        sql = """
            SELECT 
                s.name,
                s.grade,
                s.phone,
                COUNT(DISTINCT cs.id) as class_count
            FROM student s
            LEFT JOIN course_schedule cs ON cs.student_id = s.id
                OR FIND_IN_SET(s.id, cs.student_ids) > 0
            WHERE cs.class_date BETWEEN %s AND %s
              AND cs.status = 'scheduled'
        """
        params = [start_date, end_date]
        
        if student_ids:
            placeholders = ','.join(['%s'] * len(student_ids))
            sql += f" AND s.id IN ({placeholders})"
            params.extend(student_ids)
        
        sql += " GROUP BY s.id, s.name, s.grade, s.phone ORDER BY s.grade, s.name"
        
        cur.execute(sql, params)
        data = cur.fetchall()
        cur.close()
        db.close()
        
        # 创建Excel文件
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"{semester_key}课时统计"
        
        # 设置表头
        headers = ["学生姓名", "年级", "联系电话", "上课次数", "总课时(2课时/次)"]
        ws.append(headers)
        
        # 设置表头样式
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
            cell.alignment = Alignment(horizontal="center")
        
        # 写入数据
        total_classes = 0
        for row in data:
            class_count = row[3] or 0
            total_hours = class_count * 2
            total_classes += class_count
            ws.append([
                row[0] or '',
                row[1] or '',
                row[2] or '',
                class_count,
                total_hours
            ])
        
        # 添加汇总行
        ws.append(["合计", "", "", total_classes, total_classes * 2])
        
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
            adjusted_width = min(max_length + 2, 25)
            ws.column_dimensions[col_letter].width = adjusted_width
        
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        
        return send_file(
            output, 
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True, 
            download_name=f'{semester_key}_课时统计.xlsx'
        )
    except Exception as e:
        print(f"导出学期报表错误: {str(e)}")
        return jsonify({"code": 500, "msg": str(e)}), 500


# ==================== 基于排课表的课时统计模块 ====================

# ==================== 基于排课表的课时统计模块（PostgreSQL兼容版）====================

@app.route("/api/student/attendance/list", methods=["GET"])
def get_student_attendance_list():
    """获取学生的出勤记录列表"""
    try:
        student_id = request.args.get('student_id', type=int)
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        if not student_id:
            return jsonify({"code": 400, "msg": "请选择学生"}), 400
        
        db = get_db()
        cur = db.cursor()
        
        student_id_str = str(student_id)
        
        sql = """
            SELECT 
                cs.id,
                cs.class_date,
                cs.class_time,
                cs.subject,
                cs.classroom,
                cs.status,
                t.name as teacher_name
            FROM course_schedule cs
            LEFT JOIN teacher t ON cs.teacher_id = t.id
            WHERE cs.status != 'cancelled'
              AND (
                  cs.student_id = %s 
                  OR cs.student_ids = %s
                  OR cs.student_ids LIKE %s
                  OR cs.student_ids LIKE %s
                  OR cs.student_ids LIKE %s
              )
        """
        
        params = [
            student_id,
            student_id_str,
            f'{student_id_str},%',
            f'%,{student_id_str}',
            f'%,{student_id_str},%'
        ]
        
        if start_date:
            sql += " AND cs.class_date >= %s"
            params.append(start_date)
        if end_date:
            sql += " AND cs.class_date <= %s"
            params.append(end_date)
        
        sql += " ORDER BY cs.class_date DESC, cs.class_time"
        
        cur.execute(sql, params)
        data = cur.fetchall()
        cur.close()
        db.close()
        
        now = datetime.now()
        today_date = now.date()
        now_time_str = now.strftime("%H:%M")
        
        result = []
        for row in data:
            class_date = row[1]
            class_time = row[2]
            status = row[5]
            
            start_time_str = class_time.split('-')[0].strip() if class_time else "00:00"
            
            is_completed = False
            if class_date < today_date:
                is_completed = True
            elif class_date == today_date and start_time_str <= now_time_str:
                is_completed = True
            
            if is_completed and status != 'cancelled':
                actual_status = 'completed'
                status_text = '已上课'
            elif status == 'cancelled':
                actual_status = 'cancelled'
                status_text = '已取消'
            else:
                actual_status = 'scheduled'
                status_text = '待上课'
            
            result.append({
                "id": row[0],
                "class_date": str(class_date),
                "class_time": class_time,
                "subject": row[3] or '',
                "classroom": row[4] or '',
                "status": actual_status,
                "status_text": status_text,
                "teacher_name": row[6] or '待分配'
            })
        
        return jsonify({"code": 200, "data": result})
    except Exception as e:
        print(f"获取出勤记录错误: {str(e)}")
        return jsonify({"code": 500, "msg": str(e)}), 500


@app.route("/api/student/attendance/statistics", methods=["GET"])
def get_student_attendance_statistics():
    """获取学生出勤统计"""
    try:
        student_id = request.args.get('student_id', type=int)
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        if not student_id:
            return jsonify({"code": 400, "msg": "请选择学生"}), 400
        
        db = get_db()
        cur = db.cursor()
        
        student_id_str = str(student_id)
        
        sql = """
            SELECT 
                cs.class_date,
                cs.class_time,
                cs.status
            FROM course_schedule cs
            WHERE cs.status != 'cancelled'
              AND (
                  cs.student_id = %s 
                  OR cs.student_ids = %s
                  OR cs.student_ids LIKE %s
                  OR cs.student_ids LIKE %s
                  OR cs.student_ids LIKE %s
              )
        """
        
        params = [
            student_id,
            student_id_str,
            f'{student_id_str},%',
            f'%,{student_id_str}',
            f'%,{student_id_str},%'
        ]
        
        if start_date:
            sql += " AND cs.class_date >= %s"
            params.append(start_date)
        if end_date:
            sql += " AND cs.class_date <= %s"
            params.append(end_date)
        
        cur.execute(sql, params)
        data = cur.fetchall()
        cur.close()
        db.close()
        
        now = datetime.now()
        today_date = now.date()
        now_time_str = now.strftime("%H:%M")
        
        total_classes = 0
        completed_classes = 0
        upcoming_classes = 0
        
        for row in data:
            class_date = row[0]
            class_time = row[1]
            status = row[2]
            
            if status == 'cancelled':
                continue
            
            total_classes += 1
            
            start_time_str = class_time.split('-')[0].strip() if class_time else "00:00"
            
            if class_date < today_date:
                completed_classes += 1
            elif class_date == today_date and start_time_str <= now_time_str:
                completed_classes += 1
            else:
                upcoming_classes += 1
        
        total_hours = total_classes * 2
        completed_hours = completed_classes * 2
        upcoming_hours = upcoming_classes * 2
        
        return jsonify({
            "code": 200,
            "data": {
                "total_classes": total_classes,
                "completed_classes": completed_classes,
                "upcoming_classes": upcoming_classes,
                "total_hours": total_hours,
                "completed_hours": completed_hours,
                "upcoming_hours": upcoming_hours
            }
        })
    except Exception as e:
        print(f"获取出勤统计错误: {str(e)}")
        return jsonify({"code": 500, "msg": str(e)}), 500


@app.route("/api/student/attendance/export", methods=["POST"])
def export_attendance_report():
    """导出学生出勤报表"""
    try:
        data = request.json
        student_id = data.get('student_id')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        
        if not student_id:
            return jsonify({"code": 400, "msg": "请选择学生"}), 400
        
        db = get_db()
        cur = db.cursor()
        
        student_id_str = str(student_id)
        
        sql = """
            SELECT 
                s.name as student_name,
                s.grade,
                s.phone,
                cs.class_date,
                cs.class_time,
                cs.subject,
                cs.classroom,
                t.name as teacher_name,
                cs.status
            FROM course_schedule cs
            LEFT JOIN teacher t ON cs.teacher_id = t.id
            CROSS JOIN student s
            WHERE s.id = %s
              AND cs.status != 'cancelled'
              AND (
                  cs.student_id = %s 
                  OR cs.student_ids = %s
                  OR cs.student_ids LIKE %s
                  OR cs.student_ids LIKE %s
                  OR cs.student_ids LIKE %s
              )
        """
        
        params = [student_id, student_id, student_id_str, f'{student_id_str},%', f'%,{student_id_str}', f'%,{student_id_str},%']
        
        if start_date:
            sql += " AND cs.class_date >= %s"
            params.append(start_date)
        if end_date:
            sql += " AND cs.class_date <= %s"
            params.append(end_date)
        
        sql += " ORDER BY cs.class_date, cs.class_time"
        
        cur.execute(sql, params)
        data = cur.fetchall()
        cur.close()
        db.close()
        
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "学生出勤报表"
        
        headers = ["学生姓名", "年级", "联系电话", "上课日期", "上课时间", "课程名称", "教室", "授课教师", "状态"]
        ws.append(headers)
        
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
            cell.alignment = Alignment(horizontal="center")
        
        now = datetime.now()
        today_date = now.date()
        now_time_str = now.strftime("%H:%M")
        
        for row in data:
            class_date = row[3]
            class_time = row[4]
            status = row[8]
            
            start_time_str = class_time.split('-')[0].strip() if class_time else "00:00"
            
            if class_date < today_date:
                status_text = '已上课'
            elif class_date == today_date and start_time_str <= now_time_str:
                status_text = '已上课'
            elif status == 'cancelled':
                status_text = '已取消'
            else:
                status_text = '待上课'
            
            ws.append([
                row[0] or '',
                row[1] or '',
                row[2] or '',
                str(row[3]) if row[3] else '',
                row[4] or '',
                row[5] or '',
                row[6] or '',
                row[7] or '',
                status_text
            ])
        
        for col in ws.columns:
            max_length = 0
            col_letter = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 20)
            ws.column_dimensions[col_letter].width = adjusted_width
        
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        
        return send_file(
            output, 
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True, 
            download_name=f'学生出勤报表_{datetime.now().strftime("%Y%m%d")}.xlsx'
        )
    except Exception as e:
        print(f"导出出勤报表错误: {str(e)}")
        return jsonify({"code": 500, "msg": str(e)}), 500


# ==================== 课表导出模块 ====================

@app.route("/api/schedule/export/excel", methods=["POST"])
def export_schedule_excel():
    """导出周课表为Excel"""
    try:
        data = request.json
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        week_offset = data.get('week_offset', 0)
        
        if not start_date or not end_date:
            return jsonify({"code": 400, "msg": "缺少日期参数"}), 400
        
        db = get_db()
        cur = db.cursor()
        
        # 获取周课表数据
        cur.execute("""
            SELECT 
                s.id,
                s.class_date,
                EXTRACT(DOW FROM s.class_date) as weekday,
                s.class_time,
                s.subject,
                s.classroom,
                COALESCE(s.status, 'scheduled') as status,
                t.name as teacher_name,
                s.student_ids
            FROM course_schedule s
            LEFT JOIN teacher t ON s.teacher_id = t.id
            WHERE s.class_date BETWEEN %s AND %s
              AND (s.status IS NULL OR s.status != 'cancelled')
            ORDER BY s.class_date, s.class_time
        """, (start_date, end_date))
        
        data = cur.fetchall()
        
        # 获取学生名称映射
        cur.execute("SELECT id, name FROM student")
        students_map = {row[0]: row[1] for row in cur.fetchall()}
        
        cur.close()
        db.close()
        
        # 整理数据结构
        weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
        time_slots = set()
        schedule_data = {}
        
        for row in data:
            weekday_idx = int(row[2])
            weekday_idx = 6 if weekday_idx == 0 else weekday_idx - 1
            weekday_name = weekdays[weekday_idx]
            class_time = row[3]
            time_slots.add(class_time)
            
            # 解析学生名称
            student_names = []
            if row[8]:
                student_ids = [int(x) for x in row[8].split(',') if x]
                student_names = [students_map.get(sid, '') for sid in student_ids if students_map.get(sid)]
            
            key = f"{weekday_name}_{class_time}"
            schedule_data[key] = {
                "subject": row[4] or '',
                "teacher": row[7] or '',
                "classroom": row[5] or '',
                "students": '、'.join(student_names) if student_names else '集体课',
                "status": '已完成' if row[6] == 'completed' else '待上课'
            }
        
        # 排序时间段
        def get_start_time(time_str):
            return time_str.split('-')[0] if time_str else '00:00'
        sorted_time_slots = sorted(list(time_slots), key=get_start_time)
        
        # 创建工作簿
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"周课表_{start_date}_至_{end_date}"
        
        # 设置表头样式
        header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=12)
        center_alignment = Alignment(horizontal="center", vertical="center")
        
        # 第一行：标题
        ws.merge_cells('A1:F1')
        title_cell = ws['A1']
        title_cell.value = f"课 程 表  ({start_date} ~ {end_date})"
        title_cell.font = Font(bold=True, size=16)
        title_cell.alignment = center_alignment
        
        # 第二行：表头
        headers = ['时间', '周一', '周二', '周三', '周四', '周五', '周六', '周日']
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=2, column=col, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = center_alignment
        
        # 写入课程数据
        row_num = 3
        for time_slot in sorted_time_slots:
            ws.cell(row=row_num, column=1, value=time_slot).alignment = center_alignment
            
            for col, weekday in enumerate(weekdays, 2):
                key = f"{weekday}_{time_slot}"
                if key in schedule_data:
                    course = schedule_data[key]
                    cell_value = f"{course['subject']}\n{course['teacher']}\n{course['classroom']}\n{course['students']}"
                    cell = ws.cell(row=row_num, column=col, value=cell_value)
                    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                else:
                    cell = ws.cell(row=row_num, column=col, value="")
                    cell.alignment = center_alignment
            
            row_num += 1
        
        # 设置行高和列宽
        ws.row_dimensions[1].height = 30
        ws.row_dimensions[2].height = 25
        
        for row in range(3, row_num):
            ws.row_dimensions[row].height = 80
        
        for col in range(1, 9):
            ws.column_dimensions[chr(64 + col)].width = 18
        
        # 添加边框
        thin_border = openpyxl.styles.Border(
            left=openpyxl.styles.Side(style='thin'),
            right=openpyxl.styles.Side(style='thin'),
            top=openpyxl.styles.Side(style='thin'),
            bottom=openpyxl.styles.Side(style='thin')
        )
        
        for row in ws.iter_rows(min_row=2, max_row=row_num-1, min_col=1, max_col=8):
            for cell in row:
                cell.border = thin_border
        
        # 保存到内存
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        
        return send_file(
            output, 
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True, 
            download_name=f'周课表_{start_date}_至_{end_date}.xlsx'
        )
    except Exception as e:
        print(f"导出课表错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"code": 500, "msg": str(e)}), 500

# ------------------- 启动应用 -------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
