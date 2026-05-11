from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import psycopg2
import os
from datetime import datetime, timedelta
import pandas as pd

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

# ------------------- 登录 -------------------
@app.route("/api/login", methods=["POST"])
def login():
    d = request.json
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id,name,role FROM \"user\" WHERE phone=%s AND password=%s",
                (d['phone'], d['password']))
    user = cur.fetchone()
    cur.close()
    db.close()
    if user:
        return jsonify({"code":200,"data":{"id":user[0],"name":user[1],"role":user[2]}})
    return jsonify({"code":403,"msg":"账号或密码错误"})

# ------------------- 用户管理 -------------------
@app.route("/api/user/list")
def user_list():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id,phone,name,role FROM \"user\"")
    data = cur.fetchall()
    cur.close()
    db.close()
    return jsonify({"code":200,"data":data})

@app.route("/api/user/add", methods=["POST"])
def user_add():
    d = request.json
    db = get_db()
    cur = db.cursor()
    cur.execute("INSERT INTO \"user\" (phone,password,name,role) VALUES (%s,%s,%s,%s)",
                (d['phone'],d['password'],d['name'],d['role']))
    db.commit()
    cur.close()
    db.close()
    return jsonify({"code":200,"msg":"成功"})

@app.route("/api/user/update", methods=["POST"])
def user_update():
    d = request.json
    db = get_db()
    cur = db.cursor()
    cur.execute("UPDATE \"user\" SET phone=%s,name=%s,role=%s WHERE id=%s",
                (d['phone'],d['name'],d['role'],d['id']))
    db.commit()
    cur.close()
    db.close()
    return jsonify({"code":200,"msg":"成功"})

@app.route("/api/user/delete", methods=["POST"])
def user_delete():
    id = request.json.get('id')
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM \"user\" WHERE id=%s", (id,))
    db.commit()
    cur.close()
    db.close()
    return jsonify({"code":200,"msg":"成功"})

# ------------------- 学生管理 -------------------
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
    cur.execute("INSERT INTO student (name,phone,grade,school) VALUES (%s,%s,%s,%s)",
                (d['name'],d['phone'],d['grade'],d['school']))
    db.commit()
    cur.close()
    db.close()
    return jsonify({"code":200,"msg":"成功"})

@app.route("/api/student/update", methods=["POST"])
def student_update():
    d = request.json
    db = get_db()
    cur = db.cursor()
    cur.execute("UPDATE student SET name=%s,phone=%s,grade=%s,school=%s WHERE id=%s",
                (d['name'],d['phone'],d['grade'],d['school'],d['id']))
    db.commit()
    cur.close()
    db.close()
    return jsonify({"code":200,"msg":"成功"})

@app.route("/api/student/delete", methods=["POST"])
def student_delete():
    id = request.json.get('id')
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM student WHERE id=%s", (id,))
    db.commit()
    cur.close()
    db.close()
    return jsonify({"code":200,"msg":"成功"})

# ------------------- 教师管理 -------------------
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
    cur.execute("INSERT INTO teacher (name,phone,subject,class_fee) VALUES (%s,%s,%s,%s)",
                (d['name'],d['phone'],d['subject'],d['class_fee']))
    db.commit()
    cur.close()
    db.close()
    return jsonify({"code":200,"msg":"成功"})

@app.route("/api/teacher/update", methods=["POST"])
def teacher_update():
    d = request.json
    db = get_db()
    cur = db.cursor()
    cur.execute("UPDATE teacher SET name=%s,phone=%s,subject=%s,class_fee=%s WHERE id=%s",
                (d['name'],d['phone'],d['subject'],d['class_fee'],d['id']))
    db.commit()
    cur.close()
    db.close()
    return jsonify({"code":200,"msg":"成功"})

@app.route("/api/teacher/delete", methods=["POST"])
def teacher_delete():
    id = request.json.get('id')
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM teacher WHERE id=%s", (id,))
    db.commit()
    cur.close()
    db.close()
    return jsonify({"code":200,"msg":"成功"})

# ------------------- 课时管理 -------------------
@app.route("/api/course/list")
def course_list():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT cp.id,s.name,cp.total,cp.used,cp.surplus FROM course_package cp JOIN student s ON cp.student_id=s.id")
    data = cur.fetchall()
    cur.close()
    db.close()
    return jsonify({"code":200,"data":data})

@app.route("/api/course/add", methods=["POST"])
def course_add():
    d = request.json
    db = get_db()
    cur = db.cursor()
    cur.execute("INSERT INTO course_package (student_id,total,used,surplus) VALUES (%s,%s,0,%s)",
                (d['student_id'],d['total'],d['total']))
    db.commit()
    cur.close()
    db.close()
    return jsonify({"code":200,"msg":"成功"})

# ------------------- 排课日历 + 冲突 + 批量 -------------------
@app.route("/api/schedule/calendar")
def schedule_calendar():
    date = request.args.get('date')
    db = get_db()
    cur = db.cursor()
    cur.execute('''SELECT cs.id,s.name,t.name,cs.subject,cs.class_time,cs.classroom,cs.teacher_id
                   FROM course_schedule cs
                   JOIN student s ON cs.student_id=s.id
                   JOIN teacher t ON cs.teacher_id=t.id
                   WHERE cs.class_date=%s''', (date,))
    data = cur.fetchall()
    cur.close()
    db.close()
    return jsonify({"code":200,"data":data})

@app.route("/api/schedule/check", methods=["POST"])
def schedule_check():
    d = request.json
    db = get_db()
    cur = db.cursor()
    cur.execute('''SELECT COUNT(*) FROM course_schedule
                   WHERE (teacher_id=%s OR classroom=%s)
                   AND class_date=%s AND class_time=%s AND id!=%s''',
                (d['tid'],d['room'],d['date'],d['time'],d.get('id',0)))
    cnt = cur.fetchone()[0]
    cur.close()
    db.close()
    return jsonify({"code":200,"conflict":cnt>0})

@app.route("/api/schedule/save", methods=["POST"])
def schedule_save():
    d = request.json
    db = get_db()
    cur = db.cursor()
    days = []
    if int(d.get('repeat',0))==1:
        base = datetime.strptime(d['date'],"%Y-%m-%d")
        for i in range(8):
            days.append((base+timedelta(days=i*7)).strftime("%Y-%m-%d"))
    else:
        days.append(d['date'])
    for day in days:
        wd = datetime.strptime(day,"%Y-%m-%d").weekday()+1
        cur.execute('''INSERT INTO course_schedule
                       (student_id,teacher_id,subject,class_date,class_time,classroom,week_day,repeat_type)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (d['sid'],d['tid'],d['subject'],day,d['time'],d['room'],wd,d.get('repeat',0)))
    db.commit()
    cur.close()
    db.close()
    return jsonify({"code":200,"msg":"成功"})

@app.route("/api/schedule/update", methods=["POST"])
def schedule_update():
    d = request.json
    db = get_db()
    cur = db.cursor()
    cur.execute('''UPDATE course_schedule SET student_id=%s,teacher_id=%s,subject=%s,class_date=%s,class_time=%s,classroom=%s WHERE id=%s''',
                (d['sid'],d['tid'],d['subject'],d['date'],d['time'],d['room'],d['id']))
    db.commit()
    cur.close()
    db.close()
    return jsonify({"code":200,"msg":"成功"})

@app.route("/api/schedule/delete", methods=["POST"])
def schedule_delete():
    id = request.json.get('id')
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM course_schedule WHERE id=%s", (id,))
    db.commit()
    cur.close()
    db.close()
    return jsonify({"code":200,"msg":"成功"})

# ------------------- 导出 Excel -------------------
@app.route("/api/schedule/export/excel")
def export_excel():
    date = request.args.get("date",datetime.now().strftime("%Y-%m-%d"))
    db = get_db()
    cur = db.cursor()
    cur.execute('''SELECT s.name 学生,t.name 老师,cs.subject 科目,cs.class_date 日期,cs.class_time 时间,cs.classroom 教室
                   FROM course_schedule cs
                   JOIN student s ON cs.student_id=s.id
                   JOIN teacher t ON cs.teacher_id=t.id
                   WHERE cs.class_date=%s''', (date,))
    cols = [i[0] for i in cur.description]
    rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=cols)
    fn = "/tmp/课表.xlsx"
    df.to_excel(fn, index=False)
    cur.close()
    db.close()
    return send_file(fn, as_attachment=True)

# ------------------- 自动提醒 -------------------
@app.route("/api/schedule/tomorrow")
def schedule_tomorrow():
    t = (datetime.now()+timedelta(days=1)).strftime("%Y-%m-%d")
    db = get_db()
    cur = db.cursor()
    cur.execute('''SELECT s.name,s.phone,t.name,cs.subject,cs.class_time
                   FROM course_schedule cs
                   JOIN student s ON cs.student_id=s.id
                   JOIN teacher t ON cs.teacher_id=t.id
                   WHERE cs.class_date=%s''', (t,))
    data = cur.fetchall()
    cur.close()
    db.close()
    return jsonify({"code":200,"data":data})

@app.route("/")
def home():
    return "✅ 教育机构管理系统运行成功"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
