from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import sqlite3
import json
import datetime
import re
import os
import traceback

app = Flask(__name__)
CORS(app)

DB_PATH = "document_store.db"

# -------------------------
# 初始化数据库：只有一个 document 表
# -------------------------
def init_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_type TEXT,
            identifier TEXT,
            data TEXT NOT NULL,
            created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_database()

# -------------------------
# DeepSeek API（保持不变）
# -------------------------
class DeepSeekAPI:
    def __init__(self):
        self.api_key = os.getenv("DEEPSEEK_API_KEY", "sk-XXXX")
        self.base_url = "https://api.deepseek.com/chat/completions"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

    def chat_completion(self, system_prompt, user_message):
        import requests
        data = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ]
        }
        try:
            response = requests.post(self.base_url, headers=self.headers, json=data)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return {"error": str(e)}

# -------------------------
# document 操作函数
# -------------------------
def add_document(doc_type, identifier, data_dict):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        json_text = json.dumps(data_dict, ensure_ascii=False)
        cursor.execute(
            "INSERT INTO documents(doc_type, identifier, data) VALUES (?,?,?)",
            (doc_type, identifier, json_text)
        )
        conn.commit()
        new_id = cursor.lastrowid
        conn.close()
        return True, new_id
    except Exception as e:
        traceback.print_exc()
        return False, None

def query_documents(search_text=None, doc_type=None, limit=50):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    sql = "SELECT id, doc_type, identifier, data, created_time FROM documents WHERE 1=1"
    params = []

    if doc_type:
        sql += " AND doc_type = ?"
        params.append(doc_type)

    if search_text:
        sql += " AND data LIKE ?"
        params.append(f"%{search_text}%")

    sql += " ORDER BY created_time DESC LIMIT ?"
    params.append(limit)

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()

    result = []
    for r in rows:
        try:
            data_obj = json.loads(r["data"])
        except:
            data_obj = r["data"]

        result.append({
            "id": r["id"],
            "doc_type": r["doc_type"],
            "identifier": r["identifier"],
            "data": data_obj,
            "created_time": r["created_time"]
        })
    return result

# -------------------------
# AI 意图识别（保留基本逻辑）
# -------------------------
class AIAgent:
    def __init__(self):
        self.api = DeepSeekAPI()

    def analyze(self, text):
        text_lower = text.lower()
        if any(k in text_lower for k in ["查询", "查找", "搜索", "有哪些"]):
            return "query"
        if any(k in text_lower for k in ["记录", "添加", "保存"]):
            return "store"
        return "query"

    def extract_name(self, text):
        names = re.findall(r'[张李王刘陈杨黄周吴赵]{1}[\u4e00-\u9fa5]{1,2}', text)
        return names[0] if names else None

    def reply(self, user_input, db_results, context):
        system_prompt = f"""
你是一个信息系统 AI。以下是数据库返回的数据：

{json.dumps(db_results, ensure_ascii=False, indent=2)}

用户意图：{context}

请生成自然、简洁、专业的回复。
"""
        res = self.api.chat_completion(system_prompt, user_input)
        if "error" in res:
            return "AI 服务错误：" + res["error"]
        try:
            return res["choices"][0]["message"]["content"]
        except:
            return "AI 响应解析失败"

agent = AIAgent()

# -------------------------
# API：前端接口保持不变
# -------------------------

@app.route('/api/students', methods=['GET'])
def api_students():
    """返回 doc_type='student' 的文档"""
    docs = query_documents(doc_type="student", limit=200)
    return jsonify({"status": "success", "students": docs})

@app.route('/api/record', methods=['POST'])
def api_record():
    data = request.json
    student_name = data.get("student_name")
    record_type = data.get("record_type", "record")
    content = data.get("content")

    record = {
        "student_name": student_name,
        "record_type": record_type,
        "content": content,
        "time": datetime.datetime.now().isoformat()
    }

    ok, _id = add_document("record", student_name, record)
    if ok:
        return jsonify({"status": "success", "message": "记录已添加"})
    else:
        return jsonify({"status": "error", "message": "添加失败"})

import csv
import io

@app.route('/api/import', methods=['POST'])
def api_import():
    """
    支持三种导入方式：
    1. JSON 数组
    2. CSV 文件上传
    3. 自动构建 data 字典
    """
    try:
        # ---------------------------
        # 情况 1：JSON 批量导入
        # ---------------------------
        if request.is_json:
            items = request.get_json()
            if not isinstance(items, list):
                return jsonify({"status": "error", "message": "JSON 必须是数组"}), 400

            results = []
            succ = 0
            fail = 0

            for it in items:
                doc_type = it.get("doc_type")
                identifier = it.get("identifier")

                # data 字段自动构建
                if "data" in it and isinstance(it["data"], dict):
                    data_dict = it["data"]
                else:
                    # 其余字段都归到 data
                    data_dict = {k: v for (k, v) in it.items()
                                 if k not in ("doc_type", "identifier")}

                ok, new_id = add_document(doc_type, identifier, data_dict)
                if ok:
                    succ += 1
                    results.append({"item": it, "status": "ok", "id": new_id})
                else:
                    fail += 1
                    results.append({"item": it, "status": "fail"})

            return jsonify({
                "status": "success",
                "summary": {"success": succ, "fail": fail},
                "details": results
            })

        # ---------------------------
        # 情况 2：CSV 文件上传
        # ---------------------------
        if "file" in request.files:
            file = request.files["file"]
            stream = io.StringIO(file.read().decode("utf-8"))
            reader = csv.DictReader(stream)

            results = []
            succ = 0
            fail = 0

            for row in reader:
                doc_type = row.get("doc_type") or None
                identifier = row.get("identifier") or None

                # 其它列全部作为 data 字段
                data_dict = {k: v for (k, v) in row.items()
                             if k not in ("doc_type", "identifier")}

                ok, new_id = add_document(doc_type, identifier, data_dict)

                if ok:
                    succ += 1
                    results.append({"row": row, "status": "ok", "id": new_id})
                else:
                    fail += 1
                    results.append({"row": row, "status": "fail"})

            return jsonify({
                "status": "success",
                "summary": {"success": succ, "fail": fail},
                "details": results
            })

        # 如果两种方式都不是
        return jsonify({
            "status": "error",
            "message": "请上传 JSON 数组或 CSV 文件"
        }), 400

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/chat', methods=['POST'])
def chat():
    user_msg = request.json.get("message", "")
    intent = agent.analyze(user_msg)
    name = agent.extract_name(user_msg)

    db_result = []

    if intent == "query":
        db_result = query_documents(search_text=name, limit=20)
        context = "查询"
    else:
        # 存储
        record = {
            "student_name": name,
            "content": user_msg,
            "time": datetime.datetime.now().isoformat()
        }
        add_document("record", name, record)
        db_result = record
        context = "记录"

    reply = agent.reply(user_msg, db_result, context)

    return jsonify({"status": "success", "response": reply})

@app.route('/api/health')
def health():
    return jsonify({"status": "ok", "time": datetime.datetime.now().isoformat()})

@app.route('/')
def index():
    return send_from_directory('.', "index.html")

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('.', path)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
