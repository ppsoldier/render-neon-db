from flask import Flask
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# 测试首页
@app.route('/')
def home():
    return "✅ 服务启动成功！终于搞定啦！"

# 测试接口
@app.route('/api/query')
def test():
    return {"code": 200, "msg": "接口正常运行"}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
