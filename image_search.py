"""DuckDuckGo image search and download for article generation."""

import requests
import re
import json
from pathlib import Path
from urllib.parse import quote
from typing import List, Dict, Optional

KB_ROOT = Path(r"C:\knowledge-base")

def search_duckduckgo_images(query: str, max_results: int = 5) -> List[Dict]:
    """Search DuckDuckGo for images related to query."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    # DuckDuckGo image search
    url = f"https://duckduckgo.com/?q={quote(query)}&iax=images&ia=images"
    
    try:
        # First request to get token
        r = requests.get(url, headers=headers, timeout=10)
        
        # Extract vqd token
        vqd_match = re.search(r'vqd=([\d-]+)', r.text)
        if not vqd_match:
            return []
        
        vqd = vqd_match.group(1)
        
        # Image search API
        api_url = f"https://duckduckgo.com/i.js?q={quote(query)}&o=json&vqd={vqd}"
        r = requests.get(api_url, headers=headers, timeout=10)
        
        data = r.json()
        results = []
        
        for img in data.get("results", [])[:max_results]:
            results.append({
                "url": img.get("image"),
                "title": img.get("title", ""),
                "width": img.get("width"),
                "height": img.get("height"),
                "source": img.get("url")
            })
        
        return results
    except Exception as e:
        print(f"Image search error: {e}")
        return []

def download_image(url: str, save_path: Path) -> bool:
    """Download image from URL to local path."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=15, stream=True)
        r.raise_for_status()
        
        save_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(save_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        
        return True
    except Exception as e:
        print(f"Download error: {e}")
        return False

def get_article_images(topic: str, category: str, slug: str, count: int = 3) -> List[Dict]:
    """Get and save images for an article."""
    # Create images folder
    img_dir = KB_ROOT / "generated_articles" / category / "images" / slug
    img_dir.mkdir(parents=True, exist_ok=True)
    
    # Search images
    images = search_duckduckgo_images(topic, max_results=count + 2)
    
    saved_images = []
    for i, img in enumerate(images[:count]):
        if not img.get("url"):
            continue
        
        # Determine extension
        ext = ".jpg"
        if ".png" in img["url"].lower():
            ext = ".png"
        elif ".webp" in img["url"].lower():
            ext = ".webp"
        
        save_path = img_dir / f"{slug}_img{i+1}{ext}"
        
        if download_image(img["url"], save_path):
            saved_images.append({
                "local_path": str(save_path),
                "alt": img.get("title", f"{topic} image {i+1}"),
                "source_url": img.get("source", "")
            })
    
    return saved_images

if __name__ == "__main__":
    # Test
    results = search_duckduckgo_images("indian temples architecture", max_results=3)
    print(f"Found {len(results)} images")
    for r in results:
        print(f"  - {r['title'][:50]}: {r['url'][:60]}...")
