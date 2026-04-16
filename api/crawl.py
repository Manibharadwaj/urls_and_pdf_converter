"""Vercel serverless function — handles crawl requests."""
import json
import io
import base64
from urllib.parse import urlparse

from crawler import crawl as run_crawl, _save_pdf_bytes


def handler(request):
    """Vercel serverless entry point."""
    if request["method"] == "OPTIONS":
        return {
            "statusCode": 200,
            "headers": {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
            "body": "",
        }

    if request["method"] != "POST":
        return {
            "statusCode": 405,
            "body": json.dumps({"error": "Method not allowed"}),
        }

    try:
        body = json.loads(request.get("body", "{}") or "{}")
    except json.JSONDecodeError:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Invalid JSON"}),
        }

    url = body.get("url", "").strip()
    max_pages = min(int(body.get("max_pages", 20)), 50)  # cap at 50 for serverless
    delay = float(body.get("delay", 0.2))
    output = body.get("output", "json")  # "json" or "pdf"

    if not url:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "URL is required"}),
        }

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Run crawl synchronously (serverless = no threads)
    try:
        data = run_crawl(url, max_pages=max_pages, delay=delay)
    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"Crawl failed: {str(e)}"}),
        }

    headers = {
        "Access-Control-Allow-Origin": "*",
        "Content-Type": "application/json",
    }

    if output == "pdf":
        pdf_bytes = _save_pdf_bytes(data)
        b64 = base64.b64encode(pdf_bytes).decode("utf-8")
        domain = urlparse(data["base_domain"]).netloc.replace("www.", "")
        return {
            "statusCode": 200,
            "headers": {
                **headers,
                "Content-Type": "application/pdf",
                "Content-Disposition": f'attachment; filename="{domain}_crawl.pdf"',
            },
            "body": b64,
            "isBase64Encoded": True,
        }

    # Default: JSON
    # Strip huge text from pages to keep response size reasonable
    for p in data.get("pages", []):
        if len(p.get("text", "")) > 5000:
            p["text"] = p["text"][:5000] + "\n...[truncated]"

    return {
        "statusCode": 200,
        "headers": {**headers, "Content-Type": "application/json"},
        "body": json.dumps(data, ensure_ascii=False),
    }