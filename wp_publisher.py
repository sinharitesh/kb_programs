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
                    scheduled_days: int = None,
                    title_override: str = None,
                    seo_title_override: str = None,
                    meta_desc_override: str = None,
                    focus_kw_override: str = None,
                    content_override: str = None) -> dict:
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

    # ── Apply overrides ──
    if title_override: title = title_override
    if seo_title_override: seo_data['seo_title'] = seo_title_override
    if meta_desc_override: seo_data['meta_description'] = meta_desc_override
    if focus_kw_override: focus_kw = focus_kw_override
    if content_override: md_text = content_override

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

        # ── Update article frontmatter with WP status ──
        fm_text = md_path.read_text(encoding='utf-8', errors='ignore')
        wp_fields = f'wp_post_id: {post_id}\nwp_post_url: "{post_url}"\nwp_published_at: "{scheduled_str}"'
        if fm_text.startswith('---'):
            end = fm_text.index('---', 3)
            existing_fm = fm_text[3:end]
            # Remove old wp_ fields if present
            cleaned = '\n'.join(l for l in existing_fm.split('\n') if not l.strip().startswith('wp_'))
            new_fm = f'---\n{cleaned}\n{wp_fields}\n---'
            md_path.write_text(new_fm + fm_text[end+3:], encoding='utf-8')

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


# ── WP Sync ───────────────────────────────────────────────────────────────────
_WP_CACHE_DIR = KB_ROOT / "wp_sync_cache"

_WP_INDIVIDUAL_DIR = _WP_CACHE_DIR / "individual"

def _save_individual_post(post_id: str, post_data: dict):
    """Save a single WP post JSON to disk for persistent offline access."""
    _WP_INDIVIDUAL_DIR.mkdir(parents=True, exist_ok=True)
    import json as _json_ind
    (_WP_INDIVIDUAL_DIR / f"{post_id}.json").write_text(_json_ind.dumps(post_data, default=str))

def load_individual_post(post_id: str) -> dict:
    """Load a single cached WP post from disk."""
    f = _WP_INDIVIDUAL_DIR / f"{post_id}.json"
    if f.exists():
        import json as _json_ind2
        return _json_ind2.loads(f.read_text())
    return {}

def update_wp_post(wp_post_id: int, title: str = None, content: str = None,
                   slug: str = None, status: str = None, date: str = None,
                   featured_media: int = None, seo_data: dict = None) -> dict:
    """Update an existing WordPress post via REST API."""
    post_data = {}
    if title is not None: post_data["title"] = title
    if content is not None: post_data["content"] = content
    if slug is not None: post_data["slug"] = slug
    if status is not None: post_data["status"] = status
    if date is not None: post_data["date"] = date
    if featured_media is not None: post_data["featured_media"] = featured_media

    if not post_data and not seo_data:
        return {"status": "error", "message": "No fields to update"}

    try:
        result = {}
        if post_data:
            r = _wp_post(f"/wp-json/wp/v2/posts/{wp_post_id}", json_data=post_data, timeout=30)
            if r.status_code != 200:
                return {"status": "error", "message": f"WP returned {r.status_code}: {r.text[:200]}"}
            result = r.json()

        if not result:
            r2 = _wp_get(f"/wp-json/wp/v2/posts/{wp_post_id}")
            if r2.status_code == 200:
                result = r2.json()

        # Update Yoast if provided
        if seo_data:
            set_yoast_meta(wp_post_id, seo_data)

        logger.info(f"Updated WP post {wp_post_id}")
        return {
            "status": "updated",
            "post_id": result["id"],
            "link": result.get("link", ""),
            "modified": result.get("modified", ""),
        }
    except Exception as e:
        logger.error(f"WP update error: {e}")
        return {"status": "error", "message": str(e)}


def fetch_wp_posts(status: str = "future,publish", per_page: int = 50, page: int = 1, use_cache: bool = False) -> dict:
    """Fetch posts from WordPress REST API. Caches results to disk for offline use."""
    cache_key = f"posts_{status}_p{page}_pp{per_page}.json"
    cache_file = _WP_CACHE_DIR / cache_key

    # Serve from cache if requested and fresh (<5 min)
    if use_cache and cache_file.exists():
        from datetime import datetime as _dt_cache
        age = (_dt_cache.now() - _dt_cache.fromtimestamp(cache_file.stat().st_mtime)).total_seconds()
        if age < 300:  # 5 min
            import json as _json_cache
            return _json_cache.loads(cache_file.read_text())

    params = {"status": status, "per_page": per_page, "page": page, "_fields": "id,title,slug,status,date,modified,link,yoast_head_json,featured_media"}
    r = _wp_get("/wp-json/wp/v2/posts", params=params, timeout=15)
    if r.status_code != 200:
        return {"status": "error", "message": f"WP API returned {r.status_code}"}

    posts = r.json()
    total = int(r.headers.get("X-WP-Total", len(posts)))
    total_pages = int(r.headers.get("X-WP-TotalPages", 1))

    # Match with local articles by slug
    import json as _json
    gen_root = KB_ROOT / "generated_articles"
    local_slugs = {}
    if gen_root.exists():
        for md_file in gen_root.rglob("*.md"):
            content = md_file.read_text(encoding='utf-8', errors='ignore')
            localslug = md_file.stem
            wp_post_id = ""
            if content.startswith('---'):
                end = content.index('---', 3)
                fm = content[3:end]
                for line in fm.split('\n'):
                    if line.startswith('wp_post_id:'):
                        wp_post_id = line.split(':',1)[1].strip()
                    elif line.startswith('slug:'):
                        localslug = line.split(':',1)[1].strip().strip('"')
            local_slugs[localslug] = {
                "file": str(md_file.relative_to(gen_root)),
                "wp_post_id": wp_post_id,
            }

    result_posts = []
    for p in posts:
        pid = str(p["id"])
        slug = p.get("slug", "")
        matched = local_slugs.get(slug)
        matched_by_id = None
        if not matched:
            for ls, ld in local_slugs.items():
                if ld["wp_post_id"] == pid:
                    matched_by_id = ld
                    matched = ld
                    break

        # Extract Yoast SEO meta fields
        yoast = p.get("yoast_head_json", {}) or {}
        yoast_title = yoast.get("title", "")
        yoast_desc = yoast.get("description", "")
        yoast_og_title = yoast.get("og_title", "")
        yoast_og_desc = yoast.get("og_description", "")
        yoast_schema = yoast.get("schema", {}).get("@graph", [])
        yoast_schema_type = yoast_schema[0].get("@type", "") if yoast_schema else ""
        # Estimate SEO score from Yoast field completeness
        score = 30
        if yoast_title: score += 20
        if yoast_desc: score += 20
        if yoast_og_title: score += 15
        if yoast_schema_type: score += 15
        wp_seo_score = min(score, 95)

        result_posts.append({
            "id": pid,
            "title": p.get("title", {}).get("rendered", ""),
            "slug": slug,
            "status": p.get("status", ""),
            "date": p.get("date", ""),
            "modified": p.get("modified", ""),
            "link": p.get("link", ""),
            "local_match": matched["file"] if matched else None,
            "local_slug": slug if matched else (matched_by_id["file"].split("/")[-1].replace(".md","") if matched_by_id else None),
            "synced": bool(matched),
            "wp_seo_score": wp_seo_score,
            "yoast_title": yoast_title,
            "yoast_meta_description": yoast_desc,
            "yoast_og_title": yoast_og_title,
            "yoast_og_description": yoast_og_desc,
            "yoast_schema_type": yoast_schema_type,
        })

    result = {
        "status": "ok",
        "posts": result_posts,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "unmatched": sum(1 for p in result_posts if not p["synced"]),
    }


    # Merge into master all_posts.json cache
    all_cache = _WP_CACHE_DIR / "all_posts.json"
    import json as _json_all
    existing = {}
    if all_cache.exists():
        try: existing = _json_all.loads(all_cache.read_text())
        except: pass
    existing_posts = existing.get("posts", [])
    existing_ids = {p["id"] for p in existing_posts}
    for p in result_posts:
        if p["id"] not in existing_ids:
            existing_posts.append(p)
            existing_ids.add(p["id"])
    existing["posts"] = existing_posts
    existing["total"] = len(existing_posts)
    existing["updated_at"] = __import__("datetime").datetime.now().isoformat()
    all_cache.write_text(_json_all.dumps(existing, default=str))

    # Save individual post JSONs for offline access
    for p in result_posts:
        _save_individual_post(p["id"], p)


    # Save to JSON cache
    _WP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    import json as _json_save2
    cache_file.write_text(_json_save2.dumps(result, default=str))

    return result

# ── Async Improve System ──────────────────────────────────────────────────────
import threading as _threading, json as _json_async, re as _re_imp, time as _time_clean
from datetime import datetime as _dt_async

_IMPROVE_JOBS_DIR = KB_ROOT / "wp_improve_jobs"

def _get_related_kb_context(title: str, content: str) -> str:
    from db import get_con
    words = _re_imp.findall(r"[A-Z][a-z]{3,}|[a-z]{5,}", title + " " + content[:500])
    terms = list(dict.fromkeys(w.lower() for w in words if len(w) > 3))[:5]
    if not terms: return ""
    con = get_con(); context_parts = []
    like = " OR ".join(["f.fact ILIKE ?" for _ in terms])
    params = [f"%{t}%" for t in terms]
    try:
        rows = con.execute(f"SELECT f.fact, r.title, r.url FROM facts f JOIN url_registry r ON r.id=f.url_id WHERE f.verified=TRUE AND ({like}) LIMIT 5", params).fetchall()
        if rows:
            context_parts.append("━━━ VERIFIED FACTS ━━━")
            for ft, rt, ru in rows:
                context_parts.append(f"- [{rt or 'Source'}]({ru})\n  {ft[:200]}")
    except: pass
    try:
        rows = con.execute(f"SELECT question, answer FROM questions_research WHERE ({like}) LIMIT 3", params).fetchall()
        if rows:
            context_parts.append("━━━ RELATED QUESTIONS ━━━")
            for q, a in rows:
                context_parts.append(f"Q: {q}\nA: {a[:200] if a else '(unanswered)'}")
    except: pass
    con.close()
    return "\n\n".join(context_parts)



def _get_internal_links(text):
    """Match article text against internal URLs JSON. Returns formatted markdown links."""
    import json as _json_imp
    try:
        f = Path(r"C:\knowledge-base\sitemap-ritsin-com\internal_urls.json")
        if not f.exists(): return ""
        data = _json_imp.loads(f.read_text())
        words = set(text.lower().split())
        scored = []
        for u in data:
            title_words = set(u["title"].lower().split()) | set(u.get("snippet","").lower().split())
            s = len(words & title_words)
            if s > 0: scored.append((s, u))
        scored.sort(key=lambda x: x[0], reverse=True)
        links = [f"- [{u['title']}]({u['url']})" for _,u in scored[:3]]
        return "\n".join(links)
    except: return ""
def _get_external_links(text, limit=2):
    """Extract relevant external URLs from KB facts matching the text."""
    from db import get_con
    import re as _re_ext
    words = _re_ext.findall(r"[A-Z][a-z]{3,}|[a-z]{5,}", text[:500])
    terms = list(dict.fromkeys(w.lower() for w in words if len(w) > 3))[:5]
    if not terms: return ""
    try:
        con = get_con()
        like = " OR ".join(["f.fact ILIKE ?" for _ in terms])
        rows = con.execute(
            f"SELECT DISTINCT r.title, r.url FROM facts f JOIN url_registry r ON r.id=f.url_id WHERE r.url LIKE 'http%%' AND ({like}) LIMIT ?",
            [f"%%{t}%%" for t in terms] + [limit]
        ).fetchall()
        con.close()
        if rows:
            return "\n".join([f"- [{r[0] or 'Source'}]({r[1]})" for r in rows])
    except: pass
    return ""

def _get_external_links(text, limit=2):
    """Extract relevant external URLs from KB facts matching the text."""
    from db import get_con
    import re as _re_ext
    words = _re_ext.findall(r"[A-Z][a-z]{3,}|[a-z]{5,}", text[:500])
    terms = list(dict.fromkeys(w.lower() for w in words if len(w) > 3))[:5]
    if not terms: return ""
    try:
        con = get_con()
        like = " OR ".join(["f.fact ILIKE ?" for _ in terms])
        rows = con.execute(
            f"SELECT DISTINCT r.title, r.url FROM facts f JOIN url_registry r ON r.id=f.url_id WHERE r.url LIKE 'http%%' AND ({like}) LIMIT ?",
            [f"%%{t}%%" for t in terms] + [limit]
        ).fetchall()
        con.close()
        if rows:
            return "\n".join([f"- [{r[0] or 'Source'}]({r[1]})" for r in rows])
    except: pass
    return ""


def _run_improve_job(job_id, wp_post_id, slug, instructions=""):
    result = {"job_id": job_id, "status": "running", "wp_post_id": wp_post_id}
    try:
        r = _wp_get(f"/wp-json/wp/v2/posts/{wp_post_id}?_embed", timeout=15)
        if r.status_code != 200:
            result.update({"status": "error", "message": f"WP fetch: {r.status_code}"})
            return _save_improve_result(job_id, result)
        post = r.json()
        html = post.get("content",{}).get("rendered","")
        title = post.get("title",{}).get("rendered","")
        plain = _re_imp.sub(r"<figure[^>]*>.*?</figure>","",html,flags=_re_imp.DOTALL)
        plain = _re_imp.sub(r"<[^>]+>","",plain)
        plain = _re_imp.sub(r"&[a-z]+;"," ",plain)
        plain = _re_imp.sub(r"\n{3,}","\n\n",plain).strip()
        if len(plain) < 100:
            result.update({"status":"error","message":"Not enough content"})
            return _save_improve_result(job_id, result)
        kb = _get_related_kb_context(title, plain)
        kb_section = "\n━━━ KB CONTEXT ━━━\n" + kb if kb else ""
        internal_links = _get_internal_links(plain[:2000] + " " + title)
        il_section = "\n━━━ SUGGESTED INTERNAL LINKS (ritsin.com) ━━━\n" + internal_links if internal_links else ""
        external_links = _get_external_links(plain[:2000] + " " + title)
        el_section = "\n━━━ SUGGESTED EXTERNAL LINKS ━━━\n" + external_links if external_links else ""
        external_links = _get_external_links(plain[:2000] + " " + title)
        el_section = "\n━━━ SUGGESTED EXTERNAL LINKS ━━━\n" + external_links if external_links else ""
        instr_line = f"\nSPECIFIC INSTRUCTIONS: {instructions}" if instructions else ""
        prompt = f"""You are an expert SEO content improver. Improve this article.
- Better hook, readability, paragraph flow
- Add 2-3 reference links from KB context
- Optimize title for SEO + CTR
- Focus keyphrase 2-3x naturally
- Passive voice < 10%, use active verbs
- Meta description < 155 chars
- Weave KB answers where appropriate{instr_line}
{kb_section}{il_section}{el_section}

TITLE: {title}
{plain[:5000]}

Return ONLY valid JSON:
{{{{"improved_title":"...","improved_content":"...","meta_description":"...","focus_keyphrase":"...","seo_score":85}}}}"""
        import requests as _req
        resp = _req.post("http://127.0.0.1:11434/api/generate",
            json={"model":"gemma3:12b","prompt":prompt,"stream":False,"options":{"temperature":0.3}},timeout=300)
        resp.raise_for_status()
        raw = resp.json()["response"]
        raw = _re_imp.sub(r"<think>.*?</think>","",raw,flags=_re_imp.DOTALL).strip()
        raw = _re_imp.sub(r"^```(?:json)?\s*","",raw,flags=_re_imp.IGNORECASE)
        raw = _re_imp.sub(r"\s*```$","",raw)
        m = _re_imp.search(r"\{.*\}",raw,_re_imp.DOTALL)
        if not m:
            result.update({"status":"error","message":"Invalid LLM response"})
            return _save_improve_result(job_id, result)
        imp = _json_async.loads(m.group())
        result.update({"status":"done","current_title":title,"current_content":plain[:3000],
            "improved_title":imp.get("improved_title",title),
            "improved_content":imp.get("improved_content",plain),
            "meta_description":imp.get("meta_description",""),
            "focus_keyphrase":imp.get("focus_keyphrase",""),
            "seo_score":imp.get("seo_score",0),"slug":slug})
    except Exception as e:
        result = {"job_id":job_id,"status":"error","message":str(e)}
    _save_improve_result(job_id, result)

def _save_improve_result(job_id, result):
    _IMPROVE_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    (_IMPROVE_JOBS_DIR / f"{job_id}.json").write_text(_json_async.dumps(result, default=str))

def queue_improve_job(wp_post_id, slug="", instructions=""):
    job_id = f"wp_improve_{wp_post_id}_{_dt_async.now().strftime('%Y%m%d_%H%M%S')}"
    result = {"job_id":job_id,"status":"queued","wp_post_id":wp_post_id}
    _save_improve_result(job_id, result)
    t = _threading.Thread(target=_run_improve_job, args=(job_id, wp_post_id, slug, instructions), daemon=True)
    t.start()
    return result

def get_improve_job_status(job_id):
    f = _IMPROVE_JOBS_DIR / f"{job_id}.json"
    if not f.exists(): return {"status":"not_found"}
    return _json_async.loads(f.read_text())


def recover_orphaned_improve_jobs():
    """On startup, re-queue any jobs that were interrupted by a restart."""
    if not _IMPROVE_JOBS_DIR.exists():
        return
    for f in _IMPROVE_JOBS_DIR.glob("wp_improve_*.json"):
        try:
            data = _json_async.loads(f.read_text())
            if data.get("status") in ("queued", "running"):
                wp_id = data.get("wp_post_id")
                slug = data.get("slug", "")
                if wp_id:
                    data["status"] = "queued"
                    _save_improve_result(data["job_id"], data)
                    t = _threading.Thread(target=_run_improve_job, args=(data["job_id"], wp_id, slug), daemon=True)
                    t.start()
        except:
            pass
