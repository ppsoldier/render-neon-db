# app.py - 最简版本，不连接数据库
from flask import Flask, jsonify

app = Flask(__name__)

@app.route('/')
def index():
    return jsonify({
        "status": "ok",
        "message": "Hello from Vercel!",
        "version": "1.0.0"
    })

@app.route('/health')
def health():
    return jsonify({"status": "healthy"})

# 如果直接运行本地测试
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
