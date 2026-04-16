"""
Web Crawler & Scraper
- Paste a URL, it crawls the entire site
- Discovers all internal + external links
- Extracts text, images, metadata from each page
- Saves structured JSON + PDF output
"""

import io
import json
import re
import sys
import time
from collections import deque
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_MAX_PAGES = 500
DEFAULT_DELAY = 0.3
REQUEST_TIMEOUT = 15
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def is_same_domain(url: str, base_domain: str) -> bool:
    parsed = urlparse(url)
    base_parsed = urlparse(base_domain)
    host = parsed.netloc.replace("www.", "")
    base_host = base_parsed.netloc.replace("www.", "")
    return host == base_host or host.endswith("." + base_host)


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def clean_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def extract_images(soup: BeautifulSoup, page_url: str) -> list[dict]:
    images = []
    seen = set()
    for img in soup.find_all("img"):
        src = img.get("src")
        if not src:
            continue
        src = urljoin(page_url, src)
        if src in seen:
            continue
        seen.add(src)
        images.append({
            "src": src,
            "alt": img.get("alt", ""),
            "title": img.get("title", ""),
        })
    return images


def extract_metadata(soup: BeautifulSoup) -> dict:
    meta = {}
    title_tag = soup.find("title")
    meta["title"] = title_tag.get_text(strip=True) if title_tag else ""
    desc = soup.find("meta", attrs={"name": "description"})
    meta["description"] = desc["content"] if desc and desc.get("content") else ""
    for tag in soup.find_all("meta", attrs={"property": re.compile(r"^og:")}):
        key = tag.get("property", "").replace("og:", "og_")
        if tag.get("content"):
            meta[key] = tag["content"]
    for tag in soup.find_all("meta", attrs={"name": re.compile(r"^twitter:")}):
        key = tag.get("name", "").replace("twitter:", "twitter_")
        if tag.get("content"):
            meta[key] = tag["content"]
    return meta


def extract_links(soup: BeautifulSoup, page_url: str) -> dict:
    internal = set()
    external = set()
    base_domain = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue
        full_url = urljoin(page_url, href)
        if urlparse(full_url).scheme not in ("http", "https"):
            continue
        norm = normalize_url(full_url)
        if is_same_domain(full_url, base_domain):
            internal.add(norm)
        else:
            external.add(norm)

    return {"internal": sorted(internal), "external": sorted(external)}


def extract_headings(soup: BeautifulSoup) -> list[dict]:
    headings = []
    for tag in soup.find_all(re.compile(r"^h[1-6]$")):
        headings.append({"level": int(tag.name[1]), "text": tag.get_text(strip=True)})
    return headings


# ---------------------------------------------------------------------------
# Page scraper
# ---------------------------------------------------------------------------
def scrape_page(url: str) -> dict | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        content_type = resp.headers.get("Content-Type", "")
        if "text/html" not in content_type:
            return None
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"url": url, "error": str(e)}

    soup = BeautifulSoup(resp.text, "html.parser")

    links = extract_links(soup, url)
    metadata = extract_metadata(soup)
    images = extract_images(soup, url)
    text = clean_text(soup)
    headings = extract_headings(soup)

    return {
        "url": url,
        "status_code": resp.status_code,
        "metadata": metadata,
        "headings": headings,
        "text": text,
        "images": images,
        "image_count": len(images),
        "links": links,
        "internal_link_count": len(links["internal"]),
        "external_link_count": len(links["external"]),
        "word_count": len(text.split()),
    }


# ---------------------------------------------------------------------------
# Crawler
# ---------------------------------------------------------------------------
def crawl(start_url: str, max_pages: int = DEFAULT_MAX_PAGES, delay: float = DEFAULT_DELAY) -> dict:
    parsed = urlparse(start_url)
    base_domain = f"{parsed.scheme}://{parsed.netloc}"
    start_url = normalize_url(start_url)

    visited: set[str] = set()
    queue: deque[str] = deque([start_url])
    pages: list[dict] = []
    all_external: set[str] = set()
    errors: list[dict] = []

    print(f"\n  Crawling: {start_url}")
    print(f"  Max pages: {max_pages} | Delay: {delay}s\n")

    while queue and len(visited) < max_pages:
        url = queue.popleft()
        url = normalize_url(url)

        if url in visited:
            continue
        visited.add(url)

        print(f"  [{len(visited):>4}/{max_pages}] {url}")

        page = scrape_page(url)
        if page is None:
            continue
        if "error" in page:
            errors.append(page)
            continue

        pages.append(page)
        all_external.update(page["links"]["external"])

        for link in page["links"]["internal"]:
            norm = normalize_url(link)
            if norm not in visited and is_same_domain(norm, base_domain):
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

    return result


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------
def _safe_text(text: str) -> str:
    return text.encode("latin-1", "replace").decode("latin-1")


def _build_pdf(data: dict):
    from fpdf import FPDF

    pdf = FPDF()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)

    def safe_mc(w, h, txt, **kw):
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(w, h, _safe_text(txt), **kw)

    def section(title, size=10):
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "B", size)
        pdf.cell(0, 6, _safe_text(title), new_x="LMARGIN", new_y="NEXT")

    def label(label_text):
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 5, _safe_text(label_text), new_x="LMARGIN", new_y="NEXT")

    # Cover page
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 24)
    pdf.ln(30)
    pdf.cell(0, 14, "Website Crawl Report", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font("Helvetica", "", 14)
    pdf.ln(5)
    pdf.cell(0, 10, _safe_text(data["base_domain"]), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(15)
    pdf.set_font("Helvetica", "", 11)
    for line in [
        f"Start URL:          {_safe_text(data['start_url'])}",
        f"Pages scraped:      {data['pages_crawled']}",
        f"Pages w/ errors:    {data['pages_with_errors']}",
        f"Internal links:     {data['total_internal_links']}",
        f"External links:     {data['total_external_links']}",
    ]:
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 7, line, new_x="LMARGIN", new_y="NEXT")

    # Each page
    for page in data.get("pages", []):
        pdf.add_page()

        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(20, 80, 160)
        safe_mc(0, 8, page["url"])
        pdf.set_text_color(0, 0, 0)

        meta = page.get("metadata", {})
        if meta.get("title"):
            pdf.set_font("Helvetica", "B", 12)
            pdf.set_x(pdf.l_margin)
            pdf.cell(0, 7, _safe_text(meta["title"]), new_x="LMARGIN", new_y="NEXT")
        if meta.get("description"):
            pdf.set_font("Helvetica", "I", 10)
            pdf.set_text_color(80, 80, 80)
            safe_mc(0, 5, meta["description"])
            pdf.set_text_color(0, 0, 0)

        pdf.ln(3)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 5, f"Status: {page.get('status_code', '?')}  |  Words: {page.get('word_count', 0)}  |  Images: {page.get('image_count', 0)}  |  Int. links: {page.get('internal_link_count', 0)}  |  Ext. links: {page.get('external_link_count', 0)}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

        headings = page.get("headings", [])
        if headings:
            section("Headings:")
            pdf.set_font("Helvetica", "", 9)
            for h in headings:
                prefix = "  " * (h["level"] - 1)
                safe_mc(0, 5, f"{prefix}H{h['level']}: {h['text']}")
            pdf.ln(2)

        text = page.get("text", "")
        if text:
            section("Page Content:")
            pdf.set_font("Helvetica", "", 9)
            for paragraph in text.split("\n"):
                paragraph = paragraph.strip()
                if not paragraph:
                    pdf.ln(3)
                    continue
                safe_mc(0, 5, paragraph)
            pdf.ln(3)

        images = page.get("images", [])
        if images:
            section(f"Images ({len(images)}):")
            pdf.set_font("Helvetica", "", 8)
            for img in images:
                line = f"  - {img['src']}"
                if img.get("alt"):
                    line += f"  [alt: {img['alt']}]"
                safe_mc(0, 4, line)
            pdf.ln(2)

        links = page.get("links", {})
        if links.get("internal"):
            section(f"Internal Links ({len(links['internal'])}):")
            pdf.set_font("Helvetica", "", 8)
            for link in links["internal"][:30]:
                safe_mc(0, 4, f"  - {link}")
            if len(links["internal"]) > 30:
                label(f"  ... and {len(links['internal']) - 30} more")
            pdf.ln(2)

        if links.get("external"):
            section(f"External Links ({len(links['external'])}):")
            pdf.set_font("Helvetica", "", 8)
            for link in links["external"][:30]:
                safe_mc(0, 4, f"  - {link}")
            if len(links["external"]) > 30:
                label(f"  ... and {len(links['external']) - 30} more")
            pdf.ln(2)

    if data.get("external_links"):
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(20, 80, 160)
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 10, "All External Links", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 8)
        for link in data["external_links"]:
            safe_mc(0, 4, f"  - {link}")

    return pdf


def _save_pdf_bytes(data: dict) -> bytes:
    pdf = _build_pdf(data)
    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()


def save_results(data: dict, output_dir: str = "output") -> tuple[str, str]:
    import os
    os.makedirs(output_dir, exist_ok=True)

    domain = urlparse(data["base_domain"]).netloc.replace("www.", "")
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    json_file = f"{domain}_{timestamp}.json"
    json_path = os.path.join(output_dir, json_file)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    pdf_file = f"{domain}_{timestamp}.pdf"
    pdf_path = os.path.join(output_dir, pdf_file)
    _build_pdf(data).output(pdf_path)

    return json_path, pdf_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    if len(sys.argv) < 2:
        print("Usage: python3.10 crawler.py <URL> [max_pages] [delay_seconds]")
        print("Example: python3.10 crawler.py https://example.com 100 0.3")
        sys.exit(1)

    start_url = sys.argv[1]
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_MAX_PAGES
    delay = float(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_DELAY

    data = crawl(start_url, max_pages=max_pages, delay=delay)
    json_path, pdf_path = save_results(data)
    print(f"\n  JSON saved to: {json_path}")
    print(f"  PDF  saved to: {pdf_path}\n")


if __name__ == "__main__":
    main()