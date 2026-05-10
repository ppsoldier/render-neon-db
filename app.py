from flask import Flask, jsonify
from flask_cors import CORS
import psycopg2
import os

app = Flask(__name__)
CORS(app)

def get_db_connection():
    return psycopg2.connect(
        host=os.environ.get('DB_HOST'),
        user=os.environ.get('DB_USER'),
        password=os.environ.get('DB_PASSWORD'),
        database=os.environ.get('DB_NAME'),
        port=5432,
        sslmode='require'
    )

# 临时接口：查询所有表名
@app.route('/api/tables')
def get_tables():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public';")
    tables = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify({"code":200,"tables":tables})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
