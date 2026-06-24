"""WordPress publishing API routes."""
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
from pathlib import Path

wp_router = APIRouter(prefix="/articles", tags=["wordpress"])


@wp_router.get("/{slug}/images-search")
async def api_article_image_search(slug: str, q: str = ""):
    """Search DuckDuckGo for images related to the article topic."""
    from image_search import search_duckduckgo_images
    results = search_duckduckgo_images(q or slug.replace('-', ' '), max_results=8)
    return JSONResponse({"images": results})


@wp_router.post("/{slug}/image-download")
async def api_article_image_download(slug: str, request: Request):
    """Download selected image to article images folder."""
    from image_search import download_image, KB_ROOT as IMG_ROOT
    data = await request.json()
    image_url = data.get("url", "")
    category = data.get("category", "")
    if not image_url:
        return JSONResponse({"status": "error", "message": "No URL"}, status_code=400)

    img_dir = IMG_ROOT / "generated_articles" / category / "images" / slug
    img_dir.mkdir(parents=True, exist_ok=True)

    # Determine extension
    ext = ".jpg"
    if ".png" in image_url.lower(): ext = ".png"
    elif ".webp" in image_url.lower(): ext = ".webp"

    # Get next index
    existing = list(img_dir.glob(f"{slug}_img*"))
    idx = len(existing) + 1
    save_path = img_dir / f"{slug}_img{idx}{ext}"

    ok = download_image(image_url, save_path)
    if ok:
        return JSONResponse({"status": "ok", "path": str(save_path), "filename": save_path.name})
    return JSONResponse({"status": "error", "message": "Download failed"}, status_code=500)


@wp_router.post("/{slug}/image-upload")
async def api_article_image_upload(slug: str, file: UploadFile = File(...), category: str = Form("")):
    """Upload a custom image for an article."""
    from image_search import KB_ROOT as IMG_ROOT
    img_dir = IMG_ROOT / "generated_articles" / category / "images" / slug
    img_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename).suffix.lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".jpg"
    existing = list(img_dir.glob(f"{slug}_img*"))
    idx = len(existing) + 1
    save_path = img_dir / f"{slug}_img{idx}{ext}"
    content = await file.read()
    save_path.write_bytes(content)
    return JSONResponse({"status": "ok", "path": str(save_path), "filename": save_path.name})


@wp_router.get("/{slug}/images")
async def api_article_images(slug: str, category: str = ""):
    """List downloaded images for an article."""
    from image_search import KB_ROOT as IMG_ROOT
    img_dir = IMG_ROOT / "generated_articles" / category / "images" / slug
    images = []
    if img_dir.exists():
        for f in sorted(img_dir.iterdir()):
            if f.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'}:
                images.append({
                    "filename": f.name,
                    "path": str(f),
                    "is_featured": f.name.startswith("featured_"),
                    "size": f.stat().st_size,
                })
    return JSONResponse({"images": images, "slug": slug})


@wp_router.delete("/{slug}/image-delete")
async def api_article_image_delete(slug: str, filename: str = ""):
    """Delete a downloaded image."""
    from image_search import KB_ROOT as IMG_ROOT
    for cat_dir in (IMG_ROOT / "generated_articles").iterdir():
        if not cat_dir.is_dir(): continue
        img_path = cat_dir / "images" / slug / filename
        if img_path.exists():
            img_path.unlink()
            return JSONResponse({"status": "deleted", "path": str(img_path)})
    return JSONResponse({"status": "not_found"}, status_code=404)


@wp_router.post("/{slug}/set-featured")
async def api_set_featured(slug: str, request: Request):
    """Set an image as featured by renaming with featured_ prefix."""
    from image_search import KB_ROOT as IMG_ROOT
    data = await request.json()
    filename = data.get("filename", "")
    category = data.get("category", "")
    if not filename:
        return JSONResponse({"status": "error", "message": "No filename"}, status_code=400)

    img_dir = IMG_ROOT / "generated_articles" / category / "images" / slug
    old_path = img_dir / filename

    # Un-feature any existing featured image
    for f in img_dir.iterdir():
        if f.name.startswith("featured_") and f.is_file():
            new_name = f.name.replace("featured_", "", 1)
            f.rename(img_dir / new_name)

    # Set this one as featured
    new_name = "featured_" + filename.replace("featured_", "")
    new_path = img_dir / new_name
    if old_path.exists():
        old_path.rename(new_path)
        return JSONResponse({"status": "ok", "path": str(new_path)})
    return JSONResponse({"status": "not_found"}, status_code=404)


@wp_router.post("/{slug}/publish-wp")
async def api_publish_wp(slug: str, request: Request):
    """Publish article to WordPress with selected images."""
    from wp_publisher import publish_article

    data = await request.json()
    category = data.get("category", "")
    featured_image = data.get("featured_image")
    inline_images = data.get("inline_images", [])
    scheduled_days = data.get("scheduled_days")

    try:
        result = publish_article(
            slug=slug,
            category=category,
            featured_image_path=featured_image,
            inline_image_paths=inline_images,
            scheduled_days=scheduled_days,
            title_override=data.get("title"),
            seo_title_override=data.get("seo_title"),
            meta_desc_override=data.get("meta_description"),
            focus_kw_override=data.get("focus_keyphrase"),
            content_override=data.get("content"),
        )
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
    if slug:
        from pathlib import Path
        from image_search import KB_ROOT as IMG_ROOT
        gen_root = IMG_ROOT / "generated_articles"
        md_matches = list(gen_root.rglob(f"{slug}.md"))
        if md_matches:
            md_text = md_matches[0].read_text(encoding='utf-8', errors='ignore')
            # Parse frontmatter
            if md_text.startswith('---'):
                end = md_text.index('---', 3)
                fm_block = md_text[3:end]
                for line in fm_block.split('\n'):
                    if ':' in line:
                        k, v = line.split(':', 1)
                        k, v = k.strip(), v.strip().strip('"')
                        seo_data[k] = v
                md_text = md_text[end+3:].strip()
            if not title:
                title = seo_data.get('title') or seo_data.get('seo_title')
            # Enrich and convert
            md_text = enrich_with_fact_urls(md_text, seo_data)
            content = md_to_html(md_text)

    # Apply user overrides
    if data.get("title"): title = data["title"]
    if data.get("content"): content = data["content"]
    if data.get("seo_title"): seo_data["seo_title"] = data["seo_title"]
    if data.get("meta_description"): seo_data["meta_description"] = data["meta_description"]
    if data.get("focus_keyphrase"): seo_data["focus_keyphrase"] = data["focus_keyphrase"]

    try:
        result = update_wp_post(
            wp_post_id=wp_post_id,
            title=title,
            content=content,
            status=data.get("status"),
            date=data.get("date"),
            seo_data=seo_data if seo_data else None,
        )
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


# ── WP Sync Routes ────────────────────────────────────────────────────────────
@wp_router.get("/wp-sync/posts")
async def api_wp_sync_posts(status: str = "future,publish", page: int = 1, per_page: int = 20):
    """Fetch WordPress posts and match with local generated articles."""
    from wp_publisher import fetch_wp_posts
    result = fetch_wp_posts(status=status, page=page, per_page=per_page)
    return JSONResponse(result)


@wp_router.get("/wp-sync/cache-status")
async def api_wp_sync_cache():
    """Check cache status."""
    from wp_publisher import _WP_CACHE_DIR
    _WP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    files = list(_WP_CACHE_DIR.glob("posts_*.json"))
    total_size = sum(f.stat().st_size for f in files)
    return JSONResponse({
        "cached": len(files) > 0,
        "file_count": len(files),
        "total_size_kb": round(total_size / 1024, 1),
    })


@wp_router.delete("/wp-sync/cache")
async def api_wp_sync_clear_cache():
    """Delete all cached WP sync data."""
    from wp_publisher import _WP_CACHE_DIR
    import shutil
    if _WP_CACHE_DIR.exists():
        shutil.rmtree(_WP_CACHE_DIR)
    _WP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return JSONResponse({"status": "cleared"})


@wp_router.post("/wp-sync/update/{wp_post_id}")
async def api_wp_sync_update(wp_post_id: int, request: Request):
    """Update a WordPress post from local article data."""
    from wp_publisher import update_wp_post, md_to_html, enrich_with_fact_urls
    data = await request.json()
    slug = data.get("slug", "")

    title = data.get("title")
    content = None
    seo_data = {}

    if slug:
        from pathlib import Path
        from image_search import KB_ROOT as IMG_ROOT
        gen_root = IMG_ROOT / "generated_articles"
        md_matches = list(gen_root.rglob(f"{slug}.md"))
        if md_matches:
            md_text = md_matches[0].read_text(encoding='utf-8', errors='ignore')
            if md_text.startswith('---'):
                end = md_text.index('---', 3)
                fm_block = md_text[3:end]
                for line in fm_block.split('\n'):
                    if ':' in line:
                        k, v = line.split(':', 1)
                        k, v = k.strip(), v.strip().strip('"')
                        seo_data[k] = v
                md_text = md_text[end+3:].strip()
            if not title:
                title = seo_data.get('title') or seo_data.get('seo_title')
            md_text = enrich_with_fact_urls(md_text, seo_data)
            content = md_to_html(md_text)

    if data.get("title"): title = data["title"]
    if data.get("content"): content = data["content"]
    if data.get("seo_title"): seo_data["seo_title"] = data["seo_title"]
    if data.get("meta_description"): seo_data["meta_description"] = data["meta_description"]
    if data.get("focus_keyphrase"): seo_data["focus_keyphrase"] = data["focus_keyphrase"]

    try:
        result = update_wp_post(
            wp_post_id=wp_post_id, title=title, content=content,
            status=data.get("status"), date=data.get("date"),
            seo_data=seo_data if seo_data else None,
        )
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
@wp_router.post("/wp-sync/pull/{wp_post_id}")
async def api_wp_sync_pull(wp_post_id: int, request: Request):
    """Pull a WordPress post down to a local .md file."""
    import re as _re
    from wp_publisher import _wp_get
    from image_search import KB_ROOT as IMG_ROOT
    from datetime import datetime as _dt

    data = await request.json()
    slug = data.get("slug", "")
    category = data.get("category", "")

    r = _wp_get(f"/wp-json/wp/v2/posts/{wp_post_id}?_embed", timeout=15)
    if r.status_code != 200:
        return JSONResponse({"status": "error", "message": f"WP fetch failed: {r.status_code}"})

    post = r.json()
    title = post.get("title", {}).get("rendered", "")
    html_content = post.get("content", {}).get("rendered", "")
    wp_slug = post.get("slug", slug)
    wp_status = post.get("status", "")
    wp_date = post.get("date", "")

    # Basic HTML to text
    plain = _re.sub(r'<figure[^>]*>.*?</figure>', '', html_content, flags=_re.DOTALL)
    plain = _re.sub(r'<img[^>]*/?>', '', plain)
    plain = _re.sub(r'<h[1-6][^>]*>', '\n\n## ', plain)
    plain = _re.sub(r'<br\s*/?>', '\n', plain)
    plain = _re.sub(r'</p>', '\n\n', plain)
    plain = _re.sub(r'<[^>]+>', '', plain)
    plain = _re.sub(r'&[a-z]+;', ' ', plain)
    plain = _re.sub(r'\n{3,}', '\n\n', plain).strip()

    cat_folder = category or "wp-imported"
    gen_root = IMG_ROOT / "generated_articles" / cat_folder
    gen_root.mkdir(parents=True, exist_ok=True)

    file_slug = wp_slug or _re.sub(r'[^a-z0-9]+', '-', title.lower())[:40].strip('-')
    fm = f"""---
title: "{title}"
slug: {file_slug}
category: {cat_folder}
wp_post_id: {wp_post_id}
wp_status: {wp_status}
wp_date: "{wp_date}"
pulled_at: "{_dt.now().isoformat()}"
---
"""
    (gen_root / f"{file_slug}.md").write_text(fm + "\n" + plain, encoding='utf-8')
    return JSONResponse({
        "status": "pulled",
        "slug": file_slug,
        "file": f"{cat_folder}/{file_slug}.md",
        "title": title,
    })


@wp_router.get("/wp-sync/view/{wp_post_id}")
async def api_wp_sync_view(wp_post_id: int, slug: str = ""):
    """Fetch WP post content for preview."""
    import re as _re
    from wp_publisher import _wp_get
    r = _wp_get(f"/wp-json/wp/v2/posts/{wp_post_id}?_embed", timeout=10)
    if r.status_code != 200:
        return JSONResponse({"error": "Post not found"}, status_code=404)
    post = r.json()
    title = post.get("title", {}).get("rendered", "")
    html = post.get("content", {}).get("rendered", "")
    plain = _re.sub(r"<figure[^>]*>.*?</figure>", "", html, flags=_re.DOTALL)
    plain = _re.sub(r"<img[^>]*/?>", "", plain)
    plain = _re.sub(r"<h[1-6][^>]*>", "\n\n## ", plain)
    plain = _re.sub(r"<br\s*/?>", "\n", plain)
    plain = _re.sub(r"</p>", "\n\n", plain)
    plain = _re.sub(r"<[^>]+>", "", plain)
    plain = _re.sub(r"&[a-z]+;", " ", plain)
    plain = _re.sub(r"\n{3,}", "\n\n", plain).strip()
    return JSONResponse({"title": title, "content": plain})

@wp_router.post("/wp-sync/improve/{wp_post_id}")
async def api_wp_sync_improve(wp_post_id: int, request: Request):
    """Fetch WP article, improve via LLM, update SEO, backup old, push to WP."""
    import re as _re, json as _json
    from wp_publisher import (
        _wp_get, _wp_post, set_yoast_meta, md_to_html,
        enrich_with_fact_urls, upload_wp_image
    )
    from image_search import KB_ROOT as IMG_ROOT

    data = await request.json()
    slug = data.get("slug", "")

    # 1. Fetch current WP post
    r = _wp_get(f"/wp-json/wp/v2/posts/{wp_post_id}?_embed", timeout=15)
    if r.status_code != 200:
        return JSONResponse({"status": "error", "message": f"WP fetch failed: {r.status_code}"}, status_code=500)

    post = r.json()
    html_content = post.get("content", {}).get("rendered", "")
    current_title = post.get("title", {}).get("rendered", "")

    # 2. Convert HTML to plain text (basic)
    plain_text = html_content
    plain_text = _re.sub(r'<figure[^>]*>.*?</figure>', '', plain_text, flags=_re.DOTALL)
    plain_text = _re.sub(r'<img[^>]*/?>', '', plain_text)
    plain_text = _re.sub(r'<[^>]+>', '', plain_text)
    plain_text = _re.sub(r'&[a-z]+;', '', plain_text)
    plain_text = _re.sub(r'\n{3,}', '\n\n', plain_text).strip()

    if not plain_text or len(plain_text) < 100:
        return JSONResponse({"status": "error", "message": "Not enough content to improve"})

    # 3. Build improvement prompt
    improve_prompt = f"""You are an expert SEO content improver. Take the following article and improve it for better engagement and SEO.

IMPROVEMENT INSTRUCTIONS:
- Make the hook more compelling
- Improve readability with better paragraph structure and flow
- Add 1-2 natural internal reference suggestions (use [suggested reference: ...] format)
- Optimize the title for both SEO and click-through
- Ensure the focus keyphrase appears naturally 2-3 times
- Add alt text descriptions for images where missing
- Keep the tone and core message intact
- Improve the meta description to be under 155 characters

CURRENT ARTICLE:
Title: {current_title}

{plain_text[:5000]}

Return ONLY valid JSON:
{{{{
  "improved_title": "Better SEO title",
  "improved_content": "Full improved article in markdown format",
  "meta_description": "Compelling meta description under 155 chars",
  "focus_keyphrase": "Main keyword phrase",
  "seo_score": 85
}}}}"""

    # 4. Call Ollama
    try:
        import requests as _requests
        resp = _requests.post("http://127.0.0.1:11434/api/generate",
            json={'model': 'gemma3:12b', 'prompt': improve_prompt, 'stream': False,
                  'options': {'temperature': 0.3}}, timeout=300)
        resp.raise_for_status()
        raw = resp.json()['response']
        raw = _re.sub(r'<think>.*?</think>', '', raw, flags=_re.DOTALL).strip()
        raw = _re.sub(r'^```(?:json)?\s*', '', raw, flags=_re.IGNORECASE)
        raw = _re.sub(r'\s*```$', '', raw)
        match = _re.search(r'\{.*\}', raw, _re.DOTALL)
        if not match:
            return JSONResponse({"status": "error", "message": "LLM response not valid JSON"})
        improved = _json.loads(match.group())
    except Exception as e:
        return JSONResponse({"status": "error", "message": f"LLM error: {e}"})

    new_title = improved.get("improved_title") or current_title
    new_content_md = improved.get("improved_content") or plain_text
    meta_desc = improved.get("meta_description", "")
    focus_kw = improved.get("focus_keyphrase", "")
    seo_score = improved.get("seo_score", 0)

    # 5. Return preview (don't push yet — user accepts separately)
    return JSONResponse({
        "status": "preview",
        "wp_post_id": wp_post_id,
        "current_title": current_title,
        "current_content": plain_text[:3000],
        "improved_title": new_title,
        "improved_content": new_content_md,
        "meta_description": meta_desc,
        "focus_keyphrase": focus_kw,
        "seo_score": seo_score,
        "slug": slug,
    })

    # OLD BACKUP+PUSH LOGIC REMOVED — see /wp-sync/accept endpoint
    if slug:
        gen_root = IMG_ROOT / "generated_articles"
        md_matches = list(gen_root.rglob(f"{slug}.md"))
        if md_matches:
            from datetime import datetime as _dt
            backup_path = md_matches[0].with_suffix(f".backup_{_dt.now().strftime('%Y%m%d_%H%M%S')}.md")
            md_matches[0].rename(backup_path)
            # Write improved version
            new_fm = f"""---
title: "{new_title}"
slug: {slug}
focus_keyphrase: "{focus_kw}"
meta_description: "{meta_desc}"
seo_score: {seo_score}
generated_at: "{_dt.now().isoformat()}"
improved_at: "{_dt.now().isoformat()}"
improved_from_wp_id: {wp_post_id}
---
"""
            backup_path.with_name(f"{slug}.md").write_text(
                new_fm + "\n" + new_content_md, encoding='utf-8')

    # 6. Convert to HTML and push to WP
    enriched_md = enrich_with_fact_urls(new_content_md, {"focus_keyphrase": focus_kw})
    html_content = md_to_html(enriched_md)

    from wp_publisher import update_wp_post
    seo_info = {
        "seo_title": new_title, "meta_description": meta_desc,
        "focus_keyphrase": focus_kw, "seo_score": seo_score,
    }
    result = update_wp_post(
        wp_post_id=wp_post_id, title=new_title, content=html_content,
        status=data.get("status"), seo_data=seo_info,
    )


@wp_router.post("/wp-sync/improve-meta/{wp_post_id}")
async def api_wp_sync_improve_meta(wp_post_id: int, request: Request):
    """Fetch WP article meta, improve Yoast fields via LLM."""
    import re as _re, json as _json
    from wp_publisher import _wp_get

    data = await request.json()
    slug = data.get("slug", "")

    # Fetch current WP post meta
    r = _wp_get(f"/wp-json/wp/v2/posts/{wp_post_id}?_embed", timeout=15)
    if r.status_code != 200:
        return JSONResponse({"status": "error", "message": f"WP fetch failed"}, status_code=500)

    post = r.json()
    current_title = post.get("title", {}).get("rendered", "")
    yoast = post.get("yoast_head_json", {}) or {}
    html_content = post.get("content", {}).get("rendered", "")

    # Extract body text
    plain = _re.sub(r"<[^>]+>", "", html_content)
    plain = _re.sub(r"&[a-z]+;", " ", plain)
    plain = _re.sub(r"\n{3,}", "\n\n", plain).strip()[:3000]

    # Build SEO improvement prompt
    prompt = f"""You are an expert SEO content analyst. Review this article and return improved Yoast fields.

CURRENT FIELDS:
Title: {current_title}
Meta Description: {yoast.get("description", "(missing)")}  
Focus Keyphrase: {yoast.get("og_title", yoast.get("title", "(missing)"))}

ARTICLE EXCERPT:
{plain[:2000]}

Return ONLY valid JSON:
{{{{
  "focus_keyphrase": "Best primary keyphrase (3-6 words)",
  "seo_title": "SEO-optimized title (max 60 chars, click-worthy)",
  "meta_description": "Compelling description (max 155 chars)",
  "seo_score": 85,
  "tags": ["tag1", "tag2", "tag3"]
}}}}"""

    try:
        import requests as _requests
        resp = _requests.post("http://127.0.0.1:11434/api/generate",
            json={'model': 'gemma3:12b', 'prompt': prompt, 'stream': False,
                  'options': {'temperature': 0.2}}, timeout=120)
        resp.raise_for_status()
        raw = resp.json()['response']
        raw = _re.sub(r'<think>.*?</think>', '', raw, flags=_re.DOTALL).strip()
        raw = _re.sub(r'^```(?:json)?\s*', '', raw, flags=_re.IGNORECASE)
        raw = _re.sub(r'\s*```$', '', raw)
        match = _re.search(r'\{.*\}', raw, _re.DOTALL)
        if not match:
            return JSONResponse({"status": "error", "message": "LLM response not valid JSON"})
        result = _json.loads(match.group())
        return JSONResponse({"status": "ok", **result})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})


@wp_router.post("/wp-sync/accept/{wp_post_id}")
async def api_wp_sync_accept(wp_post_id: int, request: Request):
    """Accept improved article: backup old, save new, push to WP."""
    import json as _json
    from wp_publisher import update_wp_post, md_to_html, enrich_with_fact_urls
    from image_search import KB_ROOT as IMG_ROOT

    data = await request.json()
    slug = data.get("slug", "")
    title = data.get("title", "")
    content_md = data.get("content", "")
    meta_desc = data.get("meta_description", "")
    focus_kw = data.get("focus_keyphrase", "")
    seo_score = data.get("seo_score", 0)

    if not content_md:
        return JSONResponse({"status": "error", "message": "No content provided"}, status_code=400)

    # Backup old version
    if slug:
        gen_root = IMG_ROOT / "generated_articles"
        md_matches = list(gen_root.rglob(f"{slug}.md"))
        if md_matches:
            from datetime import datetime as _dt
            backup_path = md_matches[0].with_suffix(f".backup_{_dt.now().strftime('%Y%m%d_%H%M%S')}.md")
            md_matches[0].rename(backup_path)
            # Write improved version
            new_fm = f"""---
title: "{title}"
slug: {slug}
focus_keyphrase: "{focus_kw}"
meta_description: "{meta_desc}"
seo_score: {seo_score}
generated_at: "{_dt.now().isoformat()}"
improved_at: "{_dt.now().isoformat()}"
improved_from_wp_id: {wp_post_id}
---
"""
            backup_path.with_name(f"{slug}.md").write_text(
                new_fm + "\n" + content_md, encoding='utf-8')

    # Convert to HTML and push to WP
    enriched_md = enrich_with_fact_urls(content_md, {"focus_keyphrase": focus_kw})
    html_content = md_to_html(enriched_md)

    seo_info = {
        "seo_title": title, "meta_description": meta_desc,
        "focus_keyphrase": focus_kw, "seo_score": seo_score,
    }
    try:
        result = update_wp_post(
            wp_post_id=wp_post_id, title=title, content=html_content,
            seo_data=seo_info,
        )
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)



@wp_router.get("/{slug}/frontmatter")
async def api_article_frontmatter(slug: str, category: str = ""):
    """Get parsed frontmatter and body for editing before publish."""
    import json
    from image_search import KB_ROOT as IMG_ROOT

    md_path = IMG_ROOT / "generated_articles" / category / f"{slug}.md"
    if not md_path.exists():
        matches = list((IMG_ROOT / "generated_articles").rglob(f"{slug}.md"))
        if matches:
            md_path = matches[0]
            if not category:
                category = md_path.parent.name
        else:
            return JSONResponse({"error": "Article not found"}, status_code=404)

    md_text = md_path.read_text(encoding='utf-8', errors='ignore')
    fm = {}
    body = md_text
    if md_text.startswith('---'):
        end = md_text.index('---', 3)
        fm_block = md_text[3:end].strip()
        for line in fm_block.split('\n'):
            if ':' in line:
                k, v = line.split(':', 1)
                k, v = k.strip(), v.strip().strip('"')
                if k in ('tags', 'focus_keyphrases', 'internal_links', 'outbound_links'):
                    try: fm[k] = json.loads(v)
                    except: fm[k] = v
                else:
                    fm[k] = v
        body = md_text[end+3:].strip()

    return JSONResponse({
        "slug": slug, "category": category,
        "frontmatter": fm, "body": body,
    })
