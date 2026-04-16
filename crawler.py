#!/usr/bin/env -S python3.10
"""
Web Crawler & Scraper
- Paste a URL, it crawls the entire site
- Discovers all internal + external links
- Extracts text, images, metadata from each page
- Saves structured JSON + PDF output
"""

import json
import io
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
DEFAULT_DELAY = 0.3  # seconds between requests (be polite)
REQUEST_TIMEOUT = 15  # seconds
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
    """Check if a URL belongs to the same domain."""
    parsed = urlparse(url)
    base_parsed = urlparse(base_domain)
    # Match on netloc, ignoring www prefix
    host = parsed.netloc.replace("www.", "")
    base_host = base_parsed.netloc.replace("www.", "")
    return host == base_host or host.endswith("." + base_host)


def normalize_url(url: str) -> str:
    """Strip fragments and trailing slashes for dedup."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def clean_text(soup: BeautifulSoup) -> str:
    """Extract visible text from a page, removing scripts/styles."""
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # Collapse excessive blank lines
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def extract_images(soup: BeautifulSoup, page_url: str) -> list[dict]:
    """Pull all images with their src, alt, and title."""
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
    """Pull SEO / Open Graph / Twitter meta tags."""
    meta = {}
    # Title
    title_tag = soup.find("title")
    meta["title"] = title_tag.get_text(strip=True) if title_tag else ""
    # Description
    desc = soup.find("meta", attrs={"name": "description"})
    meta["description"] = desc["content"] if desc and desc.get("content") else ""
    # OG tags
    for tag in soup.find_all("meta", attrs={"property": re.compile(r"^og:")}):
        key = tag.get("property", "").replace("og:", "og_")
        if tag.get("content"):
            meta[key] = tag["content"]
    # Twitter tags
    for tag in soup.find_all("meta", attrs={"name": re.compile(r"^twitter:")}):
        key = tag.get("name", "").replace("twitter:", "twitter_")
        if tag.get("content"):
            meta[key] = tag["content"]
    return meta


def extract_links(soup: BeautifulSoup, page_url: str) -> dict:
    """Return {'internal': [...], 'external': [...]} links found on the page."""
    internal = set()
    external = set()
    base_domain = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue
        full_url = urljoin(page_url, href)
        # Skip non-http
        if urlparse(full_url).scheme not in ("http", "https"):
            continue
        norm = normalize_url(full_url)
        if is_same_domain(full_url, base_domain):
            internal.add(norm)
        else:
            external.add(norm)

    return {
        "internal": sorted(internal),
        "external": sorted(external),
    }


def extract_headings(soup: BeautifulSoup) -> dict:
    """Extract h1-h6 headings with their hierarchy."""
    headings = []
    for tag in soup.find_all(re.compile(r"^h[1-6]$")):
        headings.append({
            "level": int(tag.name[1]),
            "text": tag.get_text(strip=True),
        })
    return headings


# ---------------------------------------------------------------------------
# Page scraper
# ---------------------------------------------------------------------------
def scrape_page(url: str) -> dict | None:
    """Fetch and parse a single page. Returns structured dict or None."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        content_type = resp.headers.get("Content-Type", "")
        if "text/html" not in content_type:
            return None
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"url": url, "error": str(e)}

    soup = BeautifulSoup(resp.text, "lxml")

    # Extract everything
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
    """
    Crawl a website starting from `start_url`.
    Returns a dict with all scraped pages and the site map.
    """
    parsed = urlparse(start_url)
    base_domain = f"{parsed.scheme}://{parsed.netloc}"
    start_url = normalize_url(start_url)

    visited: set[str] = set()
    queue: deque[str] = deque([start_url])
    pages: list[dict] = []
    all_external: set[str] = set()
    errors: list[dict] = []

    print(f"\n🕷  Crawling: {start_url}")
    print(f"   Max pages: {max_pages} | Delay: {delay}s\n")

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

        # Queue internal links we haven't visited
        for link in page["links"]["internal"]:
            norm = normalize_url(link)
            if norm not in visited and is_same_domain(norm, base_domain):
                queue.append(norm)

        time.sleep(delay)

    # Build output
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
# Save
# ---------------------------------------------------------------------------
def save_results(data: dict, output_dir: str = "output") -> tuple[str, str]:
    """Save crawl results as JSON + PDF. Returns (json_path, pdf_path)."""
    import os
    os.makedirs(output_dir, exist_ok=True)

    domain = urlparse(data["base_domain"]).netloc.replace("www.", "")
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    # --- JSON ---
    json_file = f"{domain}_{timestamp}.json"
    json_path = os.path.join(output_dir, json_file)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # --- PDF ---
    pdf_file = f"{domain}_{timestamp}.pdf"
    pdf_path = os.path.join(output_dir, pdf_file)
    _save_pdf(data, pdf_path)

    return json_path, pdf_path


def _safe_text(text: str) -> str:
    """Make text safe for FPDF (Latin-1 encoding)."""
    return text.encode("latin-1", "replace").decode("latin-1")


def _build_pdf(data: dict):
    """Build a FPDF object from crawl data (shared by file save and bytes)."""
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

    # --- Cover / Summary page ---
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 24)
    pdf.ln(30)
    pdf.cell(0, 14, "Website Crawl Report", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font("Helvetica", "", 14)
    pdf.ln(5)
    pdf.cell(0, 10, _safe_text(data["base_domain"]), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(15)

    pdf.set_font("Helvetica", "", 11)
    summary_lines = [
        f"Start URL:          {_safe_text(data['start_url'])}",
        f"Pages scraped:      {data['pages_crawled']}",
        f"Pages w/ errors:    {data['pages_with_errors']}",
        f"Internal links:     {data['total_internal_links']}",
        f"External links:     {data['total_external_links']}",
    ]
    for line in summary_lines:
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 7, line, new_x="LMARGIN", new_y="NEXT")

    # --- Each page ---
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
    """Generate PDF as bytes (for serverless / in-memory use)."""
    pdf = _build_pdf(data)
    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()


def _save_pdf(data: dict, filepath: str):
    """Generate a PDF report and save to file."""
    pdf = _build_pdf(data)
    pdf.output(filepath)


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------
def print_summary(data: dict):
    """Print a clean summary of what was crawled."""
    print("\n" + "=" * 60)
    print("  CRAWL SUMMARY")
    print("=" * 60)
    print(f"  Site:              {data['base_domain']}")
    print(f"  Pages scraped:     {data['pages_crawled']}")
    print(f"  Pages w/ errors:   {data['pages_with_errors']}")
    print(f"  Internal links:    {data['total_internal_links']}")
    print(f"  External links:    {data['total_external_links']}")

    # Top pages by word count
    if data["pages"]:
        top = sorted(data["pages"], key=lambda p: p.get("word_count", 0), reverse=True)[:5]
        print("\n  Top 5 pages by content:")
        for p in top:
            print(f"    {p['word_count']:>6} words | {p['url']}")

    # Top external domains
    if data["external_links"]:
        domains = {}
        for link in data["external_links"]:
            d = urlparse(link).netloc
            domains[d] = domains.get(d, 0) + 1
        top_domains = sorted(domains.items(), key=lambda x: x[1], reverse=True)[:5]
        print("\n  Top 5 external domains:")
        for d, count in top_domains:
            print(f"    {count:>4} links | {d}")

    print("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    if len(sys.argv) < 2:
        print("Usage: python3 crawler.py <URL> [max_pages] [delay_seconds]")
        print("Example: python3 crawler.py https://example.com 100 0.3")
        sys.exit(1)

    start_url = sys.argv[1]
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_MAX_PAGES
    delay = float(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_DELAY

    data = crawl(start_url, max_pages=max_pages, delay=delay)
    json_path, pdf_path = save_results(data)
    print_summary(data)
    print(f"\n  JSON saved to: {json_path}")
    print(f"  PDF  saved to: {pdf_path}\n")


if __name__ == "__main__":
    main()