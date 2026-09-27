import os
import sys
import json
import base64
import time
from collections import defaultdict, deque

from flask import Flask, jsonify, request, abort
import oracledb

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
with open(CONFIG_PATH, "r") as f:
    config = json.load(f)

DB_CONFIG = config.get("oracle", {})

app = Flask(__name__)

_REQUEST_LOG = defaultdict(deque)
RATE_LIMIT = 60
RATE_WINDOW_SECONDS = 60


@app.before_request
def rate_limit():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    ip = ip.split(",")[0].strip()
    now = time.time()
    log = _REQUEST_LOG[ip]
    while log and now - log[0] > RATE_WINDOW_SECONDS:
        log.popleft()
    if len(log) >= RATE_LIMIT:
        abort(429, description="Too many requests. Please slow down.")
    log.append(now)


def get_db_connection():
    return oracledb.connect(
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        service_name=DB_CONFIG["service_name"],
    )


@app.errorhandler(429)
def ratelimited(e):
    return jsonify({"error": str(e.description)}), 429


@app.errorhandler(Exception)
def handle_error(e):
    app.logger.exception("Unhandled error")
    return jsonify({"error": "Server error, please try again shortly."}), 500


@app.get("/")
def home():
    return jsonify({
        "status": "online",
        "service": "SCET Student Catalog API",
        "endpoints": [
            "/api/health",
            "/api/branches",
            "/api/branches/<branch_code>/components",
            "/api/branches/<branch_code>/stats"
        ]
    })


@app.get("/favicon.ico")
def favicon():
    return "", 204


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/branches")
def list_branches():
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT branch_code, branch_name FROM academic_branches ORDER BY branch_code")
        rows = [{"branch_code": r[0], "branch_name": r[1]} for r in cursor.fetchall()]
        cursor.close()
        return jsonify(rows)
    finally:
        conn.close()


@app.get("/api/branches/<branch_code>/components")
def list_components(branch_code):
    query = request.args.get("q", "").strip()

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        base_sql = """
            SELECT c.comp_id, c.comp_name, c.category, c.specifications,
                   c.total_qty, c.issued_qty, c.available_qty,
                   (SELECT COUNT(*) FROM component_images ci
                    WHERE ci.comp_id = c.comp_id AND ci.branch_code = c.branch_code) AS img_count
            FROM lab_components c
            WHERE c.branch_code = :1
        """
        params = [branch_code]

        if query:
            q = f"%{query.lower()}%"
            base_sql += " AND (LOWER(c.comp_id) LIKE :2 OR LOWER(c.comp_name) LIKE :3 OR LOWER(c.category) LIKE :4)"
            params.extend([q, q, q])

        base_sql += " ORDER BY c.comp_id"
        cursor.execute(base_sql, params)

        results = []
        for cid, cname, ccat, cspec, tot, iss, avl, img_count in cursor.fetchall():
            results.append({
                "comp_id": cid,
                "comp_name": cname,
                "category": ccat,
                "specifications": cspec,
                "total_qty": tot,
                "issued_qty": iss,
                "available_qty": avl,
                "image_count": img_count,
            })
        cursor.close()
        return jsonify(results)
    finally:
        conn.close()


@app.get("/api/branches/<branch_code>/stats")
def branch_stats(branch_code):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*), NVL(SUM(total_qty), 0), NVL(SUM(issued_qty), 0)
            FROM lab_components
            WHERE branch_code = :1
        """, (branch_code,))
        tot_items, tot_qty, tot_issued = cursor.fetchone()

        cursor.execute("""
            SELECT COUNT(*) FROM issue_records
            WHERE branch_code = :1 AND status = 'ISSUED' AND due_date < SYSDATE
        """, (branch_code,))
        tot_overdue = cursor.fetchone()[0]
        cursor.close()

        return jsonify({
            "total_items": tot_items,
            "total_qty": tot_qty,
            "total_issued": tot_issued,
            "total_overdue": tot_overdue,
        })
    finally:
        conn.close()


@app.get("/api/branches/<branch_code>/components/<comp_id>/images")
def component_images(branch_code, comp_id):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT image_data FROM component_images WHERE comp_id = :1 AND branch_code = :2 ORDER BY image_id ASC",
            (comp_id, branch_code),
        )
        images = []
        for (blob,) in cursor.fetchall():
            if blob:
                raw = blob.read() if hasattr(blob, "read") else blob
                images.append(base64.b64encode(raw).decode("ascii"))
        cursor.close()
        return jsonify(images)
    finally:
        conn.close()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)