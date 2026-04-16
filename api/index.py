"""Vercel Flask serverless app — single entry point for all routes."""
import base64
import json
import os
from io import BytesIO
from urllib.parse import urlparse

from flask import Flask, request, jsonify, send_from_directory, send_file

from crawler import crawl as run_crawl, _save_pdf_bytes

app = Flask(__name__, static_folder=None)


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(os.path.join(os.path.dirname(os.path.dirname(__file__)), "public"), "index.html")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.route("/api/crawl", methods=["POST", "OPTIONS"])
def crawl_api():
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    max_pages = min(int(data.get("max_pages", 20)), 50)
    delay = float(data.get("delay", 0.2))
    output = data.get("output", "json")

    if not url:
        return jsonify({"error": "URL is required"}), 400

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        result = run_crawl(url, max_pages=max_pages, delay=delay)
    except Exception as e:
        return jsonify({"error": f"Crawl failed: {str(e)}"}), 500

    if output == "pdf":
        pdf_bytes = _save_pdf_bytes(result)
        buf = BytesIO(pdf_bytes)
        domain = urlparse(result["base_domain"]).netloc.replace("www.", "")
        return send_file(
            buf,
            as_attachment=True,
            download_name=f"{domain}_crawl.pdf",
            mimetype="application/pdf",
        )

    # JSON — truncate huge text to keep response size reasonable
    for p in result.get("pages", []):
        if len(p.get("text", "")) > 5000:
            p["text"] = p["text"][:5000] + "\n...[truncated]"

    return jsonify(result)