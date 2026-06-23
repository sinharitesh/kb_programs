"""
WordPress publisher for KB generated articles.
Publishes markdown articles with Yoast SEO, images, and scheduling.

Credentials file path differs by OS:
  Windows: C:\\wordpress-programs\\credentials\\wp.json
  Linux:   /app/data/wp_credentials.json
"""
import os, json, re, base64, mimetypes, logging
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import mistletoe

# ── Paths ─────────────────────────────────────────────────────────────────────
if os.name == 'nt':
    KB_ROOT = Path(r"C:\knowledge-base")
    WP_CREDS_PATH = Path(r"C:\wordpress-programs\credentials\wp.json")
else:
    KB_ROOT = Path("/app/data")
    WP_CREDS_PATH = Path("/app/data/wp_credentials.json")

logger = logging.getLogger("wp_publisher")
logger.setLevel(logging.INFO)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", "%H:%M:%S"))
    logger.addHandler(ch)

# ── Load Credentials ──────────────────────────────────────────────────────────
def _load_creds():
    if not WP_CREDS_PATH.exists():
        raise FileNotFoundError(f"WP credentials not found at {WP_CREDS_PATH}")
    with open(WP_CREDS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

_creds = _load_creds()
WP_URL          = _creds["site_url"].rstrip("/")
WP_USERNAME     = _creds["username"]
WP_APP_PASSWORD = _creds["app_password"]

def _wp_auth():
    """Build Basic auth for httpx."""
    creds = f"{WP_USERNAME}:{WP_APP_PASSWORD}"
    encoded = base64.b64encode(creds.encode()).decode()
    return encoded

AUTH_B64 = _wp_auth()
AUTH_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36',
    'Authorization': f'Basic {AUTH_B64}',
}


def _wp_post(path: str, json_data: dict = None, files: dict = None, data: bytes = None,
             extra_headers: dict = None, timeout: int = 30) -> httpx.Response:
    """Make an authenticated WP API request."""
    headers = dict(AUTH_HEADERS)
    if extra_headers:
        headers.update(extra_headers)

    client_kwargs = {"headers": headers, "timeout": timeout}
    if json_data:
        return httpx.post(f"{WP_URL}{path}", json=json_data, **client_kwargs)
    elif data:
        return httpx.post(f"{WP_URL}{path}", content=data, **client_kwargs)
    else:
        return httpx.post(f"{WP_URL}{path}", **client_kwargs)


def _wp_get(path: str, params: dict = None, timeout: int = 10) -> httpx.Response:
    """Make an authenticated WP GET request."""
    return httpx.get(f"{WP_URL}{path}", headers=AUTH_HEADERS, params=params, timeout=timeout)


# ── Image upload ──────────────────────────────────────────────────────────────
def upload_wp_image(image_path: str, alt_text: str = "") -> Optional[dict]:
    """Upload an image to WordPress. Returns {id, url} or None."""
    path = Path(image_path)
    if not path.exists():
        logger.warning(f"Image not found: {path}")
        return None

    mime_type = mimetypes.guess_type(str(path))[0] or 'image/jpeg'
    extra_headers = {
        'Content-Disposition': f'attachment; filename="{path.name}"',
        'Content-Type': mime_type,
    }
    try:
        r = _wp_post("/wp-json/wp/v2/media", data=path.read_bytes(), extra_headers=extra_headers, timeout=60)
        if r.status_code == 201:
            media_id = r.json()['id']
            source_url = r.json().get('source_url', '')
            # Set alt text
            httpx.post(f"{WP_URL}/wp-json/wp/v2/media/{media_id}",
                       headers=AUTH_HEADERS, json={'alt_text': alt_text}, timeout=10)
            logger.info(f"  Uploaded: {path.name} (ID {media_id})")
            return {"id": media_id, "url": source_url}
        else:
            logger.warning(f"  Upload failed ({r.status_code}): {r.text[:200]}")
    except Exception as e:
        logger.warning(f"  Upload error: {e}")
    return None


# ── Category & Tags ───────────────────────────────────────────────────────────
def get_or_create_category(name: str) -> Optional[int]:
    """Find or create a WP category. Returns category ID."""
    if not name:
        return None
    try:
        r = _wp_get("/wp-json/wp/v2/categories", params={'search': name, 'per_page': 10})
        if r.status_code == 200:
            for c in r.json():
                if c['name'].lower() == name.lower():
                    return c['id']
        r2 = _wp_post("/wp-json/wp/v2/categories", json_data={'name': name}, timeout=10)
        if r2.status_code == 201:
            return r2.json()['id']
    except Exception as e:
        logger.warning(f"  Category error: {e}")
    return None


def get_or_create_tags(tags_str: str) -> list:
    """Find or create WP tags from comma-separated string. Returns tag IDs."""
    if not tags_str:
        return []
    tag_ids = []
    for tag_name in [t.strip() for t in tags_str.split(',') if t.strip()]:
        try:
            r = _wp_get("/wp-json/wp/v2/tags", params={'search': tag_name, 'per_page': 5})
            found = False
            if r.status_code == 200:
                for t in r.json():
                    if t['name'].lower() == tag_name.lower():
                        tag_ids.append(t['id'])
                        found = True
                        break
            if not found:
                r2 = _wp_post("/wp-json/wp/v2/tags", json_data={'name': tag_name}, timeout=10)
                if r2.status_code == 201:
                    tag_ids.append(r2.json()['id'])
        except Exception as e:
            logger.warning(f"  Tag error ({tag_name}): {e}")
    return tag_ids


# ── Markdown → HTML conversion ────────────────────────────────────────────────
def _img_tag(url: str, alt: str) -> str:
    return (
        f'<figure style="text-align:center;margin:32px auto;max-width:900px;">'
        f'<img src="{url}" alt="{alt}" '
        f'style="max-width:100%;height:auto;border-radius:10px;'
        f'box-shadow:0 4px 18px rgba(0,0,0,0.13);display:block;margin:0 auto;"/>'
        f'</figure>'
    )


def md_to_html(md_text: str, inline_images: list = None) -> str:
    """Convert markdown to HTML, inserting images evenly across paragraphs."""
    text = re.sub(r'\n>\s*\*\*Meta:\*\*[^\n]*', '', md_text)
    html_body = mistletoe.markdown(text)

    if not inline_images:
        return html_body

    paragraphs = re.split(r'(</p>)', html_body, flags=re.IGNORECASE)
    para_tags = [i for i, s in enumerate(paragraphs) if s.strip().lower() == '</p>']

    if len(para_tags) < 2:
        for img in inline_images:
            html_body += _img_tag(img['url'], img.get('alt', ''))
        return html_body

    usable = para_tags[1:-1]
    n_images = len(inline_images)
    if len(usable) >= n_images:
        step = len(usable) / n_images
        indices = [usable[int(i * step)] for i in range(n_images)]
    else:
        indices = usable[:n_images]

    for para_idx, img in zip(reversed(indices), reversed(inline_images)):
        paragraphs[para_idx] = '</p>' + _img_tag(img['url'], img.get('alt', ''))

    return ''.join(paragraphs)


# ── Yoast SEO ─────────────────────────────────────────────────────────────────
def set_yoast_meta(post_id: int, seo_data: dict):
    """Set Yoast SEO meta fields on a WP post."""
    payload = {}
    if seo_data.get('seo_title'):
        payload['_yoast_wpseo_title'] = seo_data['seo_title']
    if seo_data.get('meta_description'):
        payload['_yoast_wpseo_metadesc'] = seo_data['meta_description']
    if seo_data.get('focus_keyphrase'):
        payload['_yoast_wpseo_focuskw'] = seo_data['focus_keyphrase']
    if seo_data.get('canonical'):
        payload['_yoast_wpseo_canonical'] = seo_data['canonical']

    if not payload:
        return

    try:
        r = _wp_post(f"/wp-json/wp/v2/posts/{post_id}", json_data=payload, timeout=10)
        if r.status_code != 200:
            logger.warning(f"  Yoast meta set failed ({r.status_code})")
        else:
            logger.info(f"  Yoast SEO meta set")
    except Exception as e:
        logger.warning(f"  Yoast meta error: {e}")


# ── Enrich with verified fact URLs ─────────────────────────────────────────────
def enrich_with_fact_urls(md_text: str, seo_data: dict, count: int = 2) -> str:
    """Add reference URLs from verified facts into the article."""
    from db import get_con
    focus_kw = seo_data.get('focus_keyphrase', '')
    if not focus_kw:
        return md_text

    con = get_con()
    rows = con.execute("""
        SELECT DISTINCT r.url, r.title
        FROM facts f
        JOIN url_registry r ON r.id = f.url_id
        WHERE f.verified = TRUE AND f.source = 'wikipedia'
          AND (f.fact ILIKE ? OR r.title ILIKE ?)
        LIMIT ?
    """, [f"%{focus_kw}%", f"%{focus_kw}%", count * 2]).fetchall()
    con.close()

    if not rows:
        return md_text

    links = "\n".join([f"- [{r[1] or 'Reference'}]({r[0]})" for r in rows[:count]])
    if "## References" not in md_text and "## Further Reading" not in md_text:
        md_text += f"\n\n## References\n\n{links}\n"
    else:
        md_text += f"\n{links}\n"

    return md_text


# ── Main publish function ─────────────────────────────────────────────────────
def publish_article(slug: str, category: str = "",
                    featured_image_path: str = None,
                    inline_image_paths: list = None,
                    scheduled_days: int = None) -> dict:
    """
    Publish a generated article to WordPress.

    Args:
        slug: Article slug (matches the .md filename)
        category: WordPress category name
        featured_image_path: Local path to featured image
        inline_image_paths: List of local paths for inline images
        scheduled_days: Days from now to schedule (7-15, random if None)

    Returns:
        dict with status, post_id, post_url, scheduled_date
    """
    import random

    # ── Load markdown ──
    md_path = KB_ROOT / "generated_articles" / category / f"{slug}.md"
    if not md_path.exists():
        md_path = list((KB_ROOT / "generated_articles").rglob(f"{slug}.md"))
        if md_path:
            md_path = md_path[0]
            if not category:
                category = md_path.parent.name
        else:
            return {"status": "error", "message": f"Article not found: {slug}.md"}

    md_text = md_path.read_text(encoding='utf-8', errors='ignore')

    # ── Parse frontmatter ──
    seo_data = {}
    title = slug.replace('-', ' ').title()
    if md_text.startswith('---'):
        end = md_text.index('---', 3)
        fm = md_text[3:end].strip()
        for line in fm.split('\n'):
            if ':' in line:
                k, v = line.split(':', 1)
                k, v = k.strip(), v.strip().strip('"')
                if k in ('tags', 'focus_keyphrases'):
                    try: seo_data[k] = json.loads(v)
                    except: seo_data[k] = v
                else:
                    seo_data[k] = v
        md_text = md_text[end+3:].strip()
    title = seo_data.get('title') or seo_data.get('seo_title') or title
    focus_kw = seo_data.get('focus_keyphrase') or seo_data.get('central_keyword') or title

    # ── Enrich with verified fact URLs ──
    md_text = enrich_with_fact_urls(md_text, seo_data)

    # ── Upload images ──
    uploaded_inline = []
    if inline_image_paths:
        for ip in inline_image_paths:
            result = upload_wp_image(ip, focus_kw)
            if result:
                uploaded_inline.append(result)

    featured_media_id = None
    if featured_image_path:
        result = upload_wp_image(featured_image_path, title)
        if result:
            featured_media_id = result['id']

    # ── Convert to HTML ──
    html_body = md_to_html(md_text, uploaded_inline)

    # ── Schedule date ──
    if scheduled_days is None:
        scheduled_days = random.randint(7, 15)
    scheduled_dt = (datetime.now(timezone.utc) + timedelta(days=scheduled_days)).replace(
        hour=random.randint(8, 10), minute=0, second=0, microsecond=0)
    scheduled_str = scheduled_dt.strftime('%Y-%m-%dT%H:%M:%S')

    # ── Category & Tags ──
    cat_id = get_or_create_category(category or seo_data.get('category', ''))
    tags = seo_data.get('tags', '')
    if isinstance(tags, list):
        tags = ','.join(tags)
    tag_ids = get_or_create_tags(tags)

    # ── Create post ──
    post_data = {
        'title': title,
        'content': html_body,
        'status': 'future',
        'date': scheduled_str,
        'slug': slug,
        'categories': [cat_id] if cat_id else [],
        'tags': tag_ids,
    }
    if featured_media_id:
        post_data['featured_media'] = featured_media_id

    try:
        r = _wp_post("/wp-json/wp/v2/posts", json_data=post_data, timeout=30)
        if r.status_code != 201:
            return {"status": "error", "message": f"WP returned {r.status_code}: {r.text[:300]}"}

        post = r.json()
        post_id = post['id']
        post_url = post.get('link', '')

        # ── Set Yoast SEO ──
        set_yoast_meta(post_id, seo_data)

        logger.info(f"Published: {title} (ID {post_id}) → {post_url} | Scheduled: {scheduled_str}")
        return {
            "status": "published",
            "post_id": post_id,
            "post_url": post_url,
            "scheduled_date": scheduled_str,
            "title": title,
        }
    except Exception as e:
        logger.error(f"Publish error: {e}")
        return {"status": "error", "message": str(e)}
