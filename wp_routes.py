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
        )
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)