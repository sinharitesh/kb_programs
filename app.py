# app.py
import json
import time
from pathlib import Path
from fastapi import FastAPI, BackgroundTasks, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from scraper import enqueue_scrape, get_job_status, results_store
from llm_enricher import process_scrape_result, update_indexes
from keyword_intelligence import run_keyword_intelligence, save_keyword_report

KB_ROOT = Path(r"C:\knowledge-base")
CONFIG = KB_ROOT / "config"

app = FastAPI(title="Knowledge Base Manager")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# ── Helpers ────────────────────────────────────────────────────
def load_categories():
    with open(CONFIG / "categories.json") as f:
        return json.load(f)["categories"]

def load_domains():
    with open(CONFIG / "domains.json") as f:
        return json.load(f)

def save_categories(cats):
    with open(CONFIG / "categories.json", "w") as f:
        json.dump({"categories": cats}, f, indent=2)

def save_domains(domains):
    with open(CONFIG / "domains.json", "w") as f:
        json.dump(domains, f, indent=2)

# ── Background pipeline ────────────────────────────────────────
def run_pipeline(job_id: str, url: str, category: str):
    while True:
        result = get_job_status(job_id)
        if result["status"] not in ("queued", "processing"):
            break
        time.sleep(2)

# Note: enrichment runs AFTER user reviews and confirms

# ── Routes ─────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    categories = load_categories()
    return templates.TemplateResponse(
        request=request, name="index.html",
        context={"categories": categories}
    )

@app.post("/ingest")
async def ingest(
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    category: str = Form(...),
    keywords: str = Form(""),
    force_refresh: bool = Form(False)
):
    job_id = enqueue_scrape(url, category, keywords, force_refresh)
    if job_id.startswith("SKIP:"):
        return JSONResponse({
            "status": "skipped",
            "message": "URL already downloaded. Enable Force Refresh to re-download.",
            "url_id": job_id.split(":")[1]
        })
    return JSONResponse({"job_id": job_id, "status": "queued"})


# Poll all jobs for queue tab
@app.get("/queue")
async def get_queue():
    return JSONResponse(results_store)

# Poll single job
@app.get("/status/{job_id}")
async def status(job_id: str):
    return JSONResponse(get_job_status(job_id))

# User confirms enrichment after review
@app.post("/enrich/{job_id}")
async def enrich_confirmed(
    job_id: str,
    category: str = Form(...),
    keywords: str = Form(...),
    background_tasks: BackgroundTasks = None
):
    result = get_job_status(job_id)
    result["keywords"] = keywords
    background_tasks.add_task(process_scrape_result, result, category)
    results_store[job_id]["status"] = "enriching"
    return JSONResponse({"status": "enriching"})

# Categories
@app.get("/categories")
async def get_categories():
    return JSONResponse({"categories": load_categories()})

@app.post("/categories/add")
async def add_category(category: str = Form(...)):
    cats = load_categories()
    if category not in cats:
        cats.append(category)
        save_categories(cats)
        (KB_ROOT / "wiki" / Path(category)).mkdir(parents=True, exist_ok=True)
        (KB_ROOT / "raw" / Path(category)).mkdir(parents=True, exist_ok=True)
    return JSONResponse({"status": "ok", "categories": cats})

@app.post("/categories/remove")
async def remove_category(category: str = Form(...)):
    cats = [c for c in load_categories() if c != category]
    save_categories(cats)
    return JSONResponse({"status": "ok", "categories": cats})

# Domains
@app.get("/domains")
async def get_domains():
    return JSONResponse(load_domains())

@app.post("/domains/add")
async def add_domain(domain: str = Form(...), list_type: str = Form(...)):
    domains = load_domains()
    if domain not in domains[list_type]:
        domains[list_type].append(domain)
        save_domains(domains)
    return JSONResponse({"status": "ok", "domains": domains})

@app.post("/domains/remove")
async def remove_domain(domain: str = Form(...), list_type: str = Form(...)):
    domains = load_domains()
    domains[list_type] = [d for d in domains[list_type] if d != domain]
    save_domains(domains)
    return JSONResponse({"status": "ok", "domains": domains})

# Wiki browser
@app.get("/wiki")
async def browse_wiki():
    wiki_root = KB_ROOT / "wiki"
    files = [str(p.relative_to(wiki_root)) for p in wiki_root.rglob("*.md")]
    return JSONResponse({"files": files})

@app.get("/wiki/file")
async def read_wiki_file(path: str):
    full_path = KB_ROOT / "wiki" / path
    if not full_path.exists():
        return JSONResponse({"error": "File not found"}, status_code=404)
    return JSONResponse({"content": full_path.read_text(encoding="utf-8")})

@app.post("/wiki/save")
async def save_wiki_file(path: str = Form(...), content: str = Form(...)):
    full_path = KB_ROOT / "wiki" / path
    if not full_path.exists():
        return JSONResponse({"error": "File not found"}, status_code=404)
    full_path.write_text(content, encoding="utf-8")
    return JSONResponse({"status": "saved"})
    
from db import get_con

@app.get("/urls")
async def get_urls(search: str = "", path: str = ""):
    con = get_con()
    query = """
        SELECT r.id, r.url, r.title, r.domain, r.quality_score,
               r.word_count, r.status, r.last_downloaded,
               STRING_AGG(p.path, ', ') as paths
        FROM url_registry r
        LEFT JOIN url_paths p ON r.id = p.url_id
        WHERE 1=1
    """
    params = []
    if search:
        query += " AND (r.title ILIKE ? OR r.url ILIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    if path:
        query += " AND p.path ILIKE ?"
        params += [f"{path}%"]
    query += " GROUP BY r.id, r.url, r.title, r.domain, r.quality_score, r.word_count, r.status, r.last_downloaded"
    results = con.execute(query, params).fetchall()
    con.close()
    return JSONResponse({"urls": [
        {"id": r[0], "url": r[1], "title": r[2], "domain": r[3],
         "quality_score": r[4], "word_count": r[5], "status": r[6],
         "last_downloaded": str(r[7]), "paths": r[8] or ""}
        for r in results
    ]})

@app.post("/urls/assign-path")
async def assign_url_path(url_id: int = Form(...), path: str = Form(...)):
    from db import assign_path
    assign_path(url_id, path)
    return JSONResponse({"status": "ok"})

@app.post("/urls/request-refresh")
async def request_refresh(url_id: int = Form(...)):
    con = get_con()
    con.execute("UPDATE url_registry SET refresh_requested=TRUE WHERE id=?", [url_id])
    con.close()
    return JSONResponse({"status": "ok"})
    
@app.post("/urls/move-path")
async def move_path(url_id: int = Form(...), old_path: str = Form(...), new_path: str = Form(...)):
    from db import move_url_path
    move_url_path(url_id, old_path, new_path)
    return JSONResponse({"status": "ok"})


@app.delete("/urls/{url_id}")
async def delete_url(url_id: int):
    con = get_con()
    con.execute("DELETE FROM url_paths WHERE url_id=?", [url_id])
    con.execute("DELETE FROM url_registry WHERE id=?", [url_id])
    con.close()
    return JSONResponse({"status": "deleted"})


@app.post("/index/rebuild")
async def rebuild_index():
    import yaml, re
    wiki_root = KB_ROOT / "wiki"
    count = 0
    # Clear existing indexes
    for f in (KB_ROOT / "index").glob("*.md"):
        f.unlink()
    (KB_ROOT / "index" / "master_crossref.md").write_text("# Master Cross-Reference\n\n")
    for md_file in wiki_root.rglob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
            match = re.match(r'^---\r?\n(.*?)\r?\n---', content, re.DOTALL)
            if not match:
                continue
            fm_text = re.sub(r'\n\n+', '\n', match.group(1))
            fm_text = re.sub(r'^(title:\s*)(.+)$', lambda m: m.group(1) + '"' + m.group(2).strip('"') + '"', fm_text, flags=re.MULTILINE)
            fm = yaml.safe_load(fm_text)
            update_indexes({
                "title": fm.get("title", ""),
                "category_path": fm.get("path", "uncategorized"),
                "tags": fm.get("tags", []),
                "cross_refs": fm.get("cross_refs", [])
            })
            count += 1
            print(f"Indexed: {fm.get('title')}")
        except Exception as e:
            print(f"Skipped {md_file.name}: {e}")
    return JSONResponse({"message": f"Rebuilt indexes from {count} wiki files"})
@app.get("/debug/wiki-files")
async def debug_wiki_files():
    wiki_root = KB_ROOT / "wiki"
    files = [str(p) for p in wiki_root.rglob("*.md")]
    return JSONResponse({"count": len(files), "files": files})
    
    
@app.post("/keywords/analyze")
async def analyze_keywords(topic: str = Form(...), category: str = Form(...)):
    #data = run_keyword_intelligence(topic) 
    data = run_keyword_intelligence(topic, category)
    filepath = save_keyword_report(topic, category, data)
    return JSONResponse({**data, "saved_to": filepath})
    
@app.get("/keywords/explore")
async def explore_keywords(
    topic: str = "", category: str = "", 
    source: str = "", min_score: int = 0
):
    con = get_con()
    query = """
        SELECT id, topic, category, source, keyword, score, notes, analyzed_at
        FROM keyword_intelligence WHERE score >= ?
    """
    params = [min_score]
    if topic:
        query += " AND topic ILIKE ?"; params.append(f"%{topic}%")
    if category:
        query += " AND category ILIKE ?"; params.append(f"%{category}%")
    if source:
        query += " AND source = ?"; params.append(source)
    query += " ORDER BY score DESC"
    rows = con.execute(query, params).fetchall()
    con.close()
    return JSONResponse({"keywords": [
        {"id": r[0], "topic": r[1], "category": r[2], "source": r[3],
         "keyword": r[4], "score": r[5], "notes": r[6], "analyzed_at": str(r[7])}
        for r in rows
    ]})
@app.get("/facts/explorer")
async def get_facts_explorer(topic: str = "", source: str = ""):
    con = get_con()
    sql = "SELECT * FROM keyword_intelligence WHERE 1=1"
    params = []
    if topic:
        sql += " AND topic LIKE ?"
        params.append(f"%{topic}%")
    if source:
        sql += " AND source = ?"
        params.append(source)
    sql += " ORDER BY analyzed_at DESC LIMIT 200"
    
    df = con.execute(sql, params).fetchdf()
    con.close()
    return {"facts": df.to_dict('records')}  

@app.get("/facts/explorer")
async def get_facts_explorer(
    topic: str = "", 
    source: str = "",
    category: str = "",
    min_score: int = 0,
    days: int = 30
):
    con = get_con()
    sql = "SELECT * FROM keyword_intelligence WHERE 1=1"
    params = []
    if topic:
        sql += " AND (topic LIKE ? OR keyword LIKE ?)"
        params.extend([f"%{topic}%", f"%{topic}%"])
    if source:
        sql += " AND source = ?"
        params.append(source)
    if category:
        sql += " AND category = ?"
        params.append(category)
    if min_score > 0:
        sql += " AND (score >= ? OR score IS NULL)"
        params.append(min_score)
    if days > 0:
        sql += " AND analyzed_at >= CURRENT_TIMESTAMP - INTERVAL ? DAY"
        params.append(days)
    sql += " ORDER BY COALESCE(score, 0) DESC, analyzed_at DESC LIMIT 200"
    
    df = con.execute(sql, params).fetchdf()
    con.close()
    return {"facts": df.to_dict('records')}

@app.get("/questions")
async def get_questions(category: str = "", keyphrase: str = "", source: str = ""):
    con = get_con()
    sql = "SELECT * FROM questions_research WHERE 1=1"
    params = []
    if category:
        sql += " AND category ILIKE ?"; params.append(f"%{category}%")
    if keyphrase:
        sql += " AND keyphrase ILIKE ?"; params.append(f"%{keyphrase}%")
    if source:
        sql += " AND source = ?"; params.append(source)
    sql += " ORDER BY analyzed_at DESC LIMIT 200"
    df = con.execute(sql, params).fetchdf()
    con.close()
    return {"questions": df.to_dict('records')}


# ── Index Management Routes ────────────────────────────────────
from db import (get_index_summary, get_index_by_category, get_keywords_index,
                get_facts_index, get_questions_index, search_index, export_index_data)
import csv, io

@app.get("/index/summary")
async def index_summary():
    return JSONResponse(get_index_summary())

@app.get("/index/categories")
async def index_categories():
    return JSONResponse({"categories": get_index_by_category()})

@app.get("/index/keywords")
async def index_keywords(topic: str = ""):
    return JSONResponse({"keywords": get_keywords_index(topic)})

@app.get("/index/facts")
async def index_facts(url_id: int = None):
    return JSONResponse({"facts": get_facts_index(url_id)})

@app.get("/index/questions")
async def index_questions(category: str = ""):
    return JSONResponse({"questions": get_questions_index(category)})

@app.get("/index/search")
async def index_search(q: str = ""):
    if not q:
        return JSONResponse({"urls": [], "facts": [], "keywords": [], "questions": []})
    return JSONResponse(search_index(q))

@app.post("/index/import-facts")
async def import_facts_from_files():
    """Backfill facts table from existing verified_facts.md files."""
    import re
    from db import get_con
    wiki_root = KB_ROOT / "wiki"
    con = get_con()
    imported = 0
    skipped = 0
    for fact_file in wiki_root.rglob("verified_facts.md"):
        content = fact_file.read_text(encoding="utf-8", errors="ignore")
        # Extract source URL from section headers like "## 📄 From: Title"
        # Match DDG snippets and Wikipedia verified entities
        # Try to find associated URL from wiki file in same folder
        wiki_files = [f for f in fact_file.parent.glob("*.md") if f.name != "verified_facts.md"]
        url_id = None
        for wf in wiki_files:
            wc = wf.read_text(encoding="utf-8", errors="ignore")
            url_match = re.search(r'^url:\s*(\S+)', wc, re.MULTILINE)
            if url_match:
                url = url_match.group(1)
                row = con.execute("SELECT id FROM url_registry WHERE url=?", [url]).fetchone()
                if row:
                    url_id = row[0]
                    break
        if not url_id:
            skipped += 1
            continue
        # Extract bullet facts from DuckDuckGo and Wikipedia sections
        facts = re.findall(r'^- (?:\[\d+\] )?(.+?)(?:\n  🔗 .+)?$', content, re.MULTILINE)
        next_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM facts").fetchone()[0]
        # Check existing facts for this url_id to avoid duplicates
        existing = set(r[0] for r in con.execute("SELECT fact FROM facts WHERE url_id=?", [url_id]).fetchall())
        for fact in facts:
            fact = fact.strip()
            if fact and fact not in existing and not fact.startswith('#'):
                verified = '📖 Wikipedia' in content and fact in content
                con.execute("INSERT INTO facts (id, url_id, fact, verified) VALUES (?,?,?,?)",
                            [next_id, url_id, fact, verified])
                next_id += 1
                imported += 1
    con.close()
    return JSONResponse({"imported": imported, "skipped_no_url": skipped})

@app.get("/index/export")
async def index_export(table: str = "url_registry", fmt: str = "json"):
    data = export_index_data(table)
    if fmt == "csv" and data:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
        from fastapi.responses import Response
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={table}.csv"}
        )
    return JSONResponse({"table": table, "count": len(data), "data": data})

# ── Delete / Cleanup Routes ────────────────────────────────────
from db import (delete_fact, delete_facts_bulk, delete_facts_by_url,
                delete_keyword, delete_keywords_by_topic, delete_keywords_bulk,
                delete_question, delete_questions_by_category, delete_questions_bulk,
                delete_url, delete_urls_below_quality, cleanup_orphans,
                get_facts_for_explorer)
from typing import List

@app.delete("/facts/{fact_id}")
async def api_delete_fact(fact_id: int):
    delete_fact(fact_id)
    return JSONResponse({"status": "deleted", "id": fact_id})

@app.post("/facts/delete-bulk")
async def api_delete_facts_bulk(ids: List[int]):
    delete_facts_bulk(ids)
    return JSONResponse({"status": "deleted", "count": len(ids)})

@app.get("/facts/explorer")
async def api_facts_explorer(verified: str = "all", search: str = ""):
    return JSONResponse({"facts": get_facts_for_explorer(verified, search)})

@app.delete("/keywords/{keyword_id}")
async def api_delete_keyword(keyword_id: int):
    delete_keyword(keyword_id)
    return JSONResponse({"status": "deleted", "id": keyword_id})

@app.post("/keywords/delete-bulk")
async def api_delete_keywords_bulk(ids: List[int]):
    delete_keywords_bulk(ids)
    return JSONResponse({"status": "deleted", "count": len(ids)})

@app.delete("/keywords/topic/{topic}")
async def api_delete_keywords_by_topic(topic: str):
    delete_keywords_by_topic(topic)
    return JSONResponse({"status": "deleted", "topic": topic})

@app.delete("/questions/{question_id}")
async def api_delete_question(question_id: int):
    delete_question(question_id)
    return JSONResponse({"status": "deleted", "id": question_id})

@app.post("/questions/delete-bulk")
async def api_delete_questions_bulk(ids: List[int]):
    delete_questions_bulk(ids)
    return JSONResponse({"status": "deleted", "count": len(ids)})

@app.delete("/questions/category/{category}")
async def api_delete_questions_by_category(category: str):
    delete_questions_by_category(category)
    return JSONResponse({"status": "deleted", "category": category})

@app.delete("/urls/{url_id}")
async def api_delete_url(url_id: int):
    delete_url(url_id)
    return JSONResponse({"status": "deleted", "id": url_id})

@app.post("/urls/delete-below-quality")
async def api_delete_below_quality(min_score: int = Form(...)):
    count = delete_urls_below_quality(min_score)
    return JSONResponse({"status": "deleted", "count": count})

@app.post("/db/cleanup-orphans")
async def api_cleanup_orphans():
    result = cleanup_orphans()
    return JSONResponse(result)