#!/usr/bin/env -S python3.10
"""
Web Crawler UI — local Flask server
Drop a URL, watch it crawl, download PDF/JSON.
"""

import json
import os
import threading
import time
import uuid
from io import BytesIO
from urllib.parse import urlparse

from flask import Flask, render_template, request, jsonify, send_file

from crawler import crawl as run_crawl, _save_pdf_bytes, save_results

app = Flask(__name__)

# In-memory job store
jobs: dict[str, dict] = {}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/crawl", methods=["POST"])
def start_crawl():
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    max_pages = int(data.get("max_pages", 50))
    delay = float(data.get("delay", 0.3))

    if not url:
        return jsonify({"error": "URL is required"}), 400

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        "id": job_id,
        "url": url,
        "status": "running",
        "progress": [],
        "result": None,
        "json_path": None,
        "pdf_path": None,
    }

    def run():
        from collections import deque
        import crawler as _c

        try:
            parsed = urlparse(url)
            base_domain = f"{parsed.scheme}://{parsed.netloc}"
            start_url = _c.normalize_url(url)

            visited = set()
            queue = deque([start_url])
            pages = []
            all_external = set()
            errors = []

            while queue and len(visited) < max_pages:
                u = queue.popleft()
                u = _c.normalize_url(u)
                if u in visited:
                    continue
                visited.add(u)

                jobs[job_id]["progress"].append({
                    "page": len(visited),
                    "total": max_pages,
                    "url": u,
                })

                page = _c.scrape_page(u)
                if page is None:
                    continue
                if "error" in page:
                    errors.append(page)
                    continue

                pages.append(page)
                all_external.update(page["links"]["external"])

                for link in page["links"]["internal"]:
                    norm = _c.normalize_url(link)
                    if norm not in visited and _c.is_same_domain(norm, base_domain):
                        queue.append(norm)

                time.sleep(delay)

            result = {
                "start_url": start_url,
                "base_domain": base_domain,
                "pages_crawled": len(pages),
                "pages_with_errors": len(errors),
                "total_internal_links": len(set(
                    link for p in pages for link in p["links"]["internal"]
                )),
                "total_external_links": len(all_external),
                "pages": pages,
                "external_links": sorted(all_external),
            }
            if errors:
                result["errors"] = errors

            json_path, pdf_path = save_results(result)
            jobs[job_id]["status"] = "done"
            jobs[job_id]["result"] = result
            jobs[job_id]["json_path"] = json_path
            jobs[job_id]["pdf_path"] = pdf_path

        except Exception as e:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = str(e)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def job_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    resp = {
        "id": job["id"],
        "status": job["status"],
        "url": job["url"],
        "progress": job["progress"][-5:],
    }

    if job["status"] == "done" and job["result"]:
        r = job["result"]
        pages_summary = []
        for p in r.get("pages", []):
            pages_summary.append({
                "url": p["url"],
                "title": p.get("metadata", {}).get("title", ""),
                "description": p.get("metadata", {}).get("description", ""),
                "status_code": p.get("status_code"),
                "word_count": p.get("word_count", 0),
                "image_count": p.get("image_count", 0),
                "internal_link_count": p.get("internal_link_count", 0),
                "external_link_count": p.get("external_link_count", 0),
                "headings": p.get("headings", []),
                "images": p.get("images", []),
                "internal_links": p.get("links", {}).get("internal", []),
                "external_links": p.get("links", {}).get("external", []),
                "text": p.get("text", ""),
            })
        resp["summary"] = {
            "pages_crawled": r["pages_crawled"],
            "pages_with_errors": r["pages_with_errors"],
            "total_internal_links": r["total_internal_links"],
            "total_external_links": r["total_external_links"],
            "pages": pages_summary,
            "external_links": r.get("external_links", []),
        }

    if job["status"] == "error":
        resp["error"] = job.get("error", "Unknown error")

    return jsonify(resp)


@app.route("/api/download/<job_id>/<file_type>")
def download(job_id, file_type):
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        return jsonify({"error": "Not ready"}), 404

    if file_type == "json" and job["json_path"]:
        return send_file(job["json_path"], as_attachment=True, mimetype="application/json")
    elif file_type == "pdf" and job["pdf_path"]:
        return send_file(job["pdf_path"], as_attachment=True, mimetype="application/pdf")
    else:
        return jsonify({"error": "Invalid file type"}), 400


if __name__ == "__main__":
    os.makedirs("output", exist_ok=True)
    print("\n  Web Crawler UI -> http://localhost:8080\n")
    app.run(debug=True, host="0.0.0.0", port=8080)