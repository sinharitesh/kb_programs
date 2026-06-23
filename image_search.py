"""Image search for article generation — uses Wikimedia Commons API (free, no key)."""

import os
from pathlib import Path
from typing import List, Dict
import httpx

if os.name == 'nt':
    KB_ROOT = Path(r"C:\knowledge-base")
else:
    KB_ROOT = Path("/app/data/kb")


def search_duckduckgo_images(query: str, max_results: int = 5) -> List[Dict]:
    """Search Wikimedia Commons for images related to query."""
    try:
        r = httpx.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query", "format": "json",
                "generator": "search", "gsrsearch": query,
                "gsrnamespace": 6, "gsrlimit": max_results,
                "prop": "imageinfo", "iiprop": "url|extmetadata",
                "iiurlwidth": 800,
            },
            headers={"User-Agent": "KBManager/1.0"},
            timeout=15
        )
        if r.status_code != 200:
            return []

        data = r.json()
        pages = data.get("query", {}).get("pages", {})
        results = []
        for pid, page in pages.items():
            info = (page.get("imageinfo") or [{}])[0]
            url = info.get("thumburl") or info.get("url", "")
            if not url:
                continue
            meta = info.get("extmetadata", {})
            title = meta.get("ImageDescription", {}).get("value", "") or page.get("title", query)
            results.append({
                "url": url,
                "title": title.replace("File:", "")[:100],
                "width": info.get("thumbwidth"),
                "height": info.get("thumbheight"),
                "source": info.get("descriptionurl", ""),
            })
        return results
    except Exception as e:
        print(f"Image search error: {e}")
        return []


def download_image(url: str, save_path: Path) -> bool:
    """Download image from URL to local path."""
    try:
        r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"},
                      timeout=15, follow_redirects=True)
        if r.status_code >= 400:
            return False
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(r.content)
        return True
    except Exception:
        return False


def get_article_images(topic: str, category: str, slug: str, count: int = 3) -> List[Dict]:
    """Get and save images for an article."""
    img_dir = KB_ROOT / "generated_articles" / category / "images" / slug
    img_dir.mkdir(parents=True, exist_ok=True)
    images = search_duckduckgo_images(topic, max_results=count + 2)
    saved_images = []
    for i, img in enumerate(images[:count]):
        if not img.get("url"): continue
        ext = ".jpg"
        if ".png" in img["url"].lower(): ext = ".png"
        elif ".webp" in img["url"].lower(): ext = ".webp"
        save_path = img_dir / f"{slug}_img{i+1}{ext}"
        if download_image(img["url"], save_path):
            saved_images.append({
                "local_path": str(save_path),
                "alt": img.get("title", f"{topic} image {i+1}"),
                "source_url": img.get("source", ""),
            })
    return saved_images