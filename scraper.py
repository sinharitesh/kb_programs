# scraper.py
import httpx
import json
import hashlib
import asyncio
import re
from pathlib import Path
from bs4 import BeautifulSoup
from datetime import datetime
from queue import Queue
from threading import Thread

KB_ROOT = Path(r"C:\knowledge-base")
RAW_ROOT = KB_ROOT / "raw"
CONFIG = KB_ROOT / "config"

# ── Domain config ──────────────────────────────────────────────
def load_domains():
    with open(CONFIG / "domains.json") as f:
        return json.load(f)

def get_domain(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc.replace("www.", "")

# ── Junk Detection ─────────────────────────────────────────────
JUNK_PHRASES = [
    "lorem ipsum", "coming soon", "under construction",
    "page not found", "403 forbidden", "404 not found",
    "access denied", "just a moment"
]

def junk_check(text: str, url: str) -> tuple[bool, str]:
    domains = load_domains()
    domain = get_domain(url)

    if domain in domains["blocklist"]:
        return False, f"Domain {domain} is blocklisted"

    if domain in domains["whitelist"]:
        return True, "Whitelisted domain"

    word_count = len(text.split())
    if word_count < 200:
        return False, f"Too short ({word_count} words)"

    for phrase in JUNK_PHRASES:
        if phrase in text.lower():
            return False, f"Junk phrase found: '{phrase}'"

    return True, "OK"

# ── Image Downloader ───────────────────────────────────────────
def download_images(soup: BeautifulSoup, save_dir: Path, base_url: str):
    from urllib.parse import urljoin
    save_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for img in soup.find_all("img", src=True)[:10]:  # max 10 images
        img_url = urljoin(base_url, img["src"])
        try:
            r = httpx.get(img_url, timeout=10, follow_redirects=True)
            if len(r.content) < 1024:  # skip tracking pixels
                continue
            ext = img_url.split(".")[-1].split("?")[0][:4]
            fname = hashlib.md5(img_url.encode()).hexdigest()[:8] + f".{ext}"
            (save_dir / fname).write_bytes(r.content)
            saved.append(fname)
        except Exception:
            continue
    return saved

# ── Core Scraper ───────────────────────────────────────────────
def scrape_url(url: str, category_path: str) -> dict:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (KnowledgeBot/1.0)"}
        r = httpx.get(url, timeout=15, follow_redirects=True, headers=headers)
        r.raise_for_status()
    except Exception as e:
        return {"status": "error", "reason": str(e), "url": url}

    soup = BeautifulSoup(r.text, "lxml")

    # Remove noise
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r'\s+', ' ', text).strip()

    ok, reason = junk_check(text, url)
    if not ok:
        return {"status": "rejected", "reason": reason, "url": url}

    # Save raw text
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = hashlib.md5(url.encode()).hexdigest()[:8]
    raw_dir = RAW_ROOT / category_path
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_file = raw_dir / f"{slug}_{ts}.txt"
    raw_file.write_text(text, encoding="utf-8")

    # Save images
    img_dir = KB_ROOT / "wiki" / category_path / "images"
    images = download_images(soup, img_dir, url)

    title = soup.title.string.strip() if soup.title else url

    return {
        "status": "ok",
        "url": url,
        "title": title,
        "raw_file": str(raw_file),
        "images": images,
        "category_path": category_path,
        "word_count": len(text.split()),
        "text": text[:5000],   # short preview
        "full_text": text      # full text for chunked enrichment  
    }

# ── Background Queue ───────────────────────────────────────────
scrape_queue = Queue()
results_store = {}  # job_id → result

from llm_enricher import process_scrape_result
from db import register_url, assign_path

def background_worker():
    print("[Worker] Thread started and waiting for jobs...")
    while True:
        
        job = scrape_queue.get()
        if job is None:
            break
        job_id, url, category_path = job["id"], job["url"], job["category_path"]
        results_store[job_id] = {**results_store[job_id], "status": "processing"}
        result = scrape_url(url, category_path)
        results_store[job_id] = {**results_store[job_id], **result}
        if result["status"] == "ok":
            results_store[job_id]["status"] = "enriching"
            enriched = process_scrape_result(result, results_store[job_id].get("category"))
            url_id = enriched.get("url_id")
            if url_id:
                assign_path(url_id, results_store[job_id].get("category", "uncategorized"))
            results_store[job_id]["status"] = "done"
        print(f"[Queue] Job {job_id} done: {results_store[job_id]['status']}")
        scrape_queue.task_done()



from db import is_url_registered

def enqueue_scrape(url: str, category_path: str, keywords: str = "", force_refresh: bool = False) -> str:
    existing = is_url_registered(url)
    if existing and not existing["refresh_requested"] and not force_refresh:
        return f"SKIP:{existing['id']}"  # already downloaded
    job_id = hashlib.md5(f"{url}{datetime.now()}".encode()).hexdigest()[:8]
    results_store[job_id] = {
        "status": "queued", "url": url,
        "category": category_path, "keywords": keywords
    }
    scrape_queue.put({"id": job_id, "url": url, "category_path": category_path})
    return job_id



def get_job_status(job_id: str) -> dict:
    return results_store.get(job_id, {"status": "not_found"})


worker_thread = Thread(target=background_worker, daemon=True)
worker_thread.start()
