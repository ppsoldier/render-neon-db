from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import pg8000
import os
from datetime import datetime, timedelta
import io
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
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
        
        # 创建课时消耗记录表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS hour_consumption (
                id SERIAL PRIMARY KEY,
                package_id INTEGER REFERENCES course_package(id),
                schedule_id INTEGER REFERENCES course_schedule(id),
                hours DECIMAL(8,2) NOT NULL,
                consume_date DATE NOT NULL,
                note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 创建索引
        cur.execute("CREATE INDEX IF NOT EXISTS idx_cs_class_date ON course_schedule(class_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_cs_teacher_id ON course_schedule(teacher_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_cs_status ON course_schedule(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_hc_package_id ON hour_consumption(package_id)")
        
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
@app.route("/api/student/hours/list", methods=["GET"])
def student_hours_list():
    """获取学生课时列表"""
    try:
        student_id = request.args.get('student_id', type=int)
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
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
        if start_date:
            sql += " AND cp.created_at >= %s"
            params.append(start_date)
        if end_date:
            sql += " AND cp.created_at <= %s"
            params.append(end_date)
        
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
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        db = get_db()
        cur = db.cursor()
        
        # 总课时统计
        sql = """
            SELECT 
                COUNT(*) as total_packages,
                COALESCE(SUM(total), 0) as total_hours,
                COALESCE(SUM(used), 0) as used_hours,
                COALESCE(SUM(surplus), 0) as surplus_hours
            FROM course_package
            WHERE status = 'active'
        """
        params = []
        if start_date:
            sql += " AND created_at >= %s"
            params.append(start_date)
        if end_date:
            sql += " AND created_at <= %s"
            params.append(end_date)
        
        cur.execute(sql, params)
        total_stats = cur.fetchone()
        
        # 即将过期课时
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
        
        return jsonify({"code": 200, "data": {
            "total_packages": total_stats[0] or 0,
            "total_hours": float(total_stats[1]) if total_stats[1] else 0,
            "used_hours": float(total_stats[2]) if total_stats[2] else 0,
            "surplus_hours": float(total_stats[3]) if total_stats[3] else 0,
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
            INSERT INTO hour_consumption (package_id, hours, consume_date, note)
            VALUES (%s, %s, CURRENT_DATE, %s)
        """, (package_id, hours, note))
        
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
                hc.created_at
            FROM hour_consumption hc
            LEFT JOIN course_package cp ON hc.package_id = cp.id
            LEFT JOIN student s ON cp.student_id = s.id
            WHERE 1=1
        """
        params = []
        
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
                "note": r[6] or ''
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
        
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "学生课时统计"
        
        headers = ["学生姓名", "年级", "联系电话", "课程名称", "总课时", "已用课时", "剩余课时", "有效期"]
        ws.append(headers)
        
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
            cell.alignment = Alignment(horizontal="center")
        
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
                "teacher_name": row[5] or
