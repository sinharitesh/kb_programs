# app.py
import json
import time
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, BackgroundTasks, Request, Form, Query
from typing import List
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from scraper import enqueue_scrape, get_job_status, results_store
from llm_enricher import process_scrape_result, update_indexes
from keyword_intelligence import run_keyword_intelligence, save_keyword_report

# Logging helper with timestamp
def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

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
async def get_urls(search: str = "", path: str = "", cat_search: str = "",
                   score_op: str = "", score_val: int = -1, sort_quality: str = ""):
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
    if cat_search:
        query += " AND p.path ILIKE ?"
        params += [f"%{cat_search}%"]
    if score_val >= 0:
        if score_op == "gte":
            query += " AND r.quality_score >= ?"
        else:  # default lte
            query += " AND (r.quality_score <= ? OR r.quality_score IS NULL)"
        params += [score_val]
    query += " GROUP BY r.id, r.url, r.title, r.domain, r.quality_score, r.word_count, r.status, r.last_downloaded"
    if sort_quality == "asc":
        query += " ORDER BY r.quality_score ASC NULLS FIRST"
    elif sort_quality == "desc":
        query += " ORDER BY r.quality_score DESC"
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
            log(f"Indexed: {fm.get('title')}")
        except Exception as e:
            log(f"Skipped {md_file.name}: {e}")
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
@app.get("/keywords/explorer")
async def get_keywords_explorer(topic: str = "", source: str = ""):
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
    return {"keywords": df.to_dict('records')}  

@app.get("/keywords/explorer2")
async def get_keywords_explorer2(
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
    return {"keywords": df.to_dict('records')}

@app.get("/questions")
async def get_questions(category: List[str] = Query(default=[]), keyphrase: str = "", source: str = ""):
    from db import get_con
    con = get_con()
    sql = "SELECT * FROM questions_research WHERE 1=1"
    params = []
    if category:
        # Use ILIKE OR for each selected category for flexible matching
        cat_clauses = ' OR '.join(['category ILIKE ?' for _ in category])
        sql += f" AND ({cat_clauses})"
        params.extend([f"%{c}%" for c in category])
    if keyphrase:
        sql += " AND keyphrase ILIKE ?"; params.append(f"%{keyphrase}%")
    if source:
        sql += " AND source = ?"; params.append(source)
    sql += " ORDER BY category, analyzed_at DESC LIMIT 500"
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

@app.get("/analysis/keywords")
async def analyze_keywords_data(search: str = ""):
    """Return aggregated keyword analysis data from DuckDB."""
    from db import get_con
    con = get_con()
    
    # Summary stats by source
    source_stats = con.execute("""
        SELECT source, COUNT(*) as count, 
               COUNT(DISTINCT topic) as topics,
               COUNT(DISTINCT category) as categories
        FROM keyword_intelligence 
        GROUP BY source
        ORDER BY count DESC
    """).fetchall()
    
    # Keywords by topic
    topic_stats = con.execute("""
        SELECT topic, category, COUNT(*) as keyword_count,
               COUNT(DISTINCT source) as sources,
               MAX(analyzed_at) as last_analyzed
        FROM keyword_intelligence 
        GROUP BY topic, category
        ORDER BY keyword_count DESC
        LIMIT 50
    """).fetchall()
    
    # Recent URLs discovered (with scrape status if available)
    # Filter by search keyword if provided
    where_clause = "WHERE ki.notes LIKE 'http%'"
    if search:
        where_clause += f" AND ki.keyword ILIKE '%{search}%'"
    
    recent_urls = con.execute(f"""
        SELECT ki.keyword, ki.source, ki.notes as url, ki.topic, ki.category,
               ki.analyzed_at, u.id as url_id, u.status
        FROM keyword_intelligence ki
        LEFT JOIN url_registry u ON ki.notes = u.url
        {where_clause}
        ORDER BY ki.analyzed_at DESC
        LIMIT 100
    """).fetchall()
    
    con.close()
    
    def fmt_dt(dt):
        return dt.isoformat() if dt else None
    
    return JSONResponse({
        "by_source": [{"source": r[0], "count": r[1], "topics": r[2], "categories": r[3]} for r in source_stats],
        "by_topic": [{"topic": r[0], "category": r[1], "keywords": r[2], "sources": r[3], "last_analyzed": fmt_dt(r[4])} for r in topic_stats],
        "urls_discovered": [{"keyword": r[0], "source": r[1], "url": r[2], "topic": r[3], "category": r[4], "discovered_at": fmt_dt(r[5]), "url_id": r[6], "status": r[7]} for r in recent_urls]
    })

@app.get("/analysis/keywords/high-potential")
async def get_high_potential_keywords(
    min_sources: int = 2,
    category: str = "",
    search: str = ""
):
    """Return keywords scored by multi-source presence and URL yield."""
    from db import get_con
    con = get_con()
    
    # Base query for keyword aggregation
    where_clauses = ["1=1"]
    if category:
        where_clauses.append(f"category = '{category}'")
    if search:
        where_clauses.append(f"keyword ILIKE '%{search}%'")
    where_sql = " AND ".join(where_clauses)
    
    # Get keyword scores
    rows = con.execute(f"""
        SELECT 
            keyword,
            topic,
            category,
            COUNT(DISTINCT source) as source_count,
            COUNT(*) as total_mentions,
            COUNT(DISTINCT CASE WHEN notes LIKE 'http%' THEN notes END) as url_count,
            MAX(analyzed_at) as last_analyzed,
            GROUP_CONCAT(DISTINCT source) as sources
        FROM keyword_intelligence
        WHERE {where_sql}
        GROUP BY keyword, topic, category
        HAVING COUNT(DISTINCT source) >= {min_sources}
        ORDER BY source_count DESC, url_count DESC, total_mentions DESC
        LIMIT 100
    """).fetchall()
    
    # Calculate potential score (0-100)
    def calc_score(r):
        sources = r[3]  # source_count
        urls = r[5]     # url_count
        mentions = r[4] # total_mentions
        # Score: sources*30 + urls*20 + log(mentions)*10, capped at 100
        return min(100, int(sources * 30 + urls * 20 + (mentions ** 0.5) * 5))
    
    keywords = [{
        "keyword": r[0],
        "topic": r[1],
        "category": r[2],
        "source_count": r[3],
        "total_mentions": r[4],
        "url_count": r[5],
        "last_analyzed": r[6].isoformat() if r[6] else None,
        "sources": r[7].split(',') if r[7] else [],
        "score": calc_score(r),
        "potential": "hot" if calc_score(r) >= 70 else "warm" if calc_score(r) >= 40 else "cold"
    } for r in rows]
    
    con.close()
    return JSONResponse({"keywords": keywords, "total": len(keywords)})

@app.get("/analysis/keywords/{keyword}/detail")
async def get_keyword_detail(keyword: str):
    """Return detailed analysis for a specific keyword."""
    from db import get_con
    con = get_con()
    
    # All mentions of this keyword
    mentions = con.execute("""
        SELECT keyword, source, topic, category, notes, analyzed_at
        FROM keyword_intelligence
        WHERE keyword = ?
        ORDER BY analyzed_at DESC
    """, [keyword]).fetchall()
    
    # URLs found for this keyword
    urls = con.execute("""
        SELECT ki.notes as url, ki.source, ki.analyzed_at, u.status, u.quality_score
        FROM keyword_intelligence ki
        LEFT JOIN url_registry u ON ki.notes = u.url
        WHERE ki.keyword = ? AND ki.notes LIKE 'http%'
        ORDER BY ki.analyzed_at DESC
    """, [keyword]).fetchall()
    
    con.close()
    
    return JSONResponse({
        "keyword": keyword,
        "mentions": [{"source": m[1], "topic": m[2], "category": m[3], "note": m[4], "at": m[5].isoformat() if m[5] else None} for m in mentions],
        "urls": [{"url": u[0], "source": u[1], "discovered_at": u[2].isoformat() if u[2] else None, "status": u[3], "quality": u[4]} for u in urls],
        "total_mentions": len(mentions),
        "urls_found": len(urls),
        "sources": list(set(m[1] for m in mentions))
    })



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
async def api_facts_explorer(verified: str = "all", search: str = "", source: str = ""):
    return JSONResponse({"facts": get_facts_for_explorer(verified, search, source)})

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

# ── Article Generation ────────────────────────────────────────────────────────
from article_generator import gather_all_context, generate_article
import hashlib

article_jobs = {}  # job_id → result

@app.post("/articles/gather-context")
async def api_gather_context(
    idea: str = Form(...),
    category: str = Form(""),
    search_phrases: str = Form(""),
    fact_limit: int = Form(7),
    question_limit: int = Form(7)
):
    """Gather facts, questions, and wiki context for article generation."""
    phrases = [p.strip() for p in search_phrases.split(",") if p.strip()] if search_phrases else None
    context = gather_all_context(idea, category, phrases, fact_limit, question_limit)
    return JSONResponse(context)

@app.post("/articles/generate")
async def api_generate_article(
    background_tasks: BackgroundTasks,
    context: str = Form(...),
    title: str = Form(""),
    keywords: str = Form(""),
    focus_keyphrase: str = Form(""),
    tone: str = Form("informative and engaging"),
    word_count: int = Form(1200),
    language: str = Form("en"),
    content_type: str = Form("Blog Post"),
    selected_fact_ids: str = Form(""),
    selected_question_ids: str = Form("")
):
    """Start async article generation with selected context."""
    ctx = json.loads(context)
    
    # Filter to selected facts/questions
    if selected_fact_ids:
        sel_ids = [int(x) for x in selected_fact_ids.split(",") if x.strip()]
        ctx["selected_facts"] = [f for f in ctx["facts"] if f["id"] in sel_ids]
    if selected_question_ids:
        sel_ids = [int(x) for x in selected_question_ids.split(",") if x.strip()]
        ctx["selected_questions"] = [q for q in ctx["questions"] if q["id"] in sel_ids]
    
    settings = {
        "title": title or ctx["idea"],
        "keywords": keywords or ctx["idea"],
        "focus_keyphrase": focus_keyphrase or keywords or ctx["idea"],
        "tone": tone,
        "word_count": word_count,
        "language": language,
        "content_type": content_type
    }
    
    job_id = hashlib.md5(f"{ctx['idea']}{datetime.now()}".encode()).hexdigest()[:8]
    article_jobs[job_id] = {"status": "generating", "started_at": datetime.now().isoformat()}
    
    def run_generation():
        try:
            result = generate_article(ctx, settings)
            article_jobs[job_id] = {**article_jobs[job_id], "status": "done", **result}
        except Exception as e:
            article_jobs[job_id] = {**article_jobs[job_id], "status": "error", "error": str(e)}
    
    background_tasks.add_task(run_generation)
    return JSONResponse({"job_id": job_id, "status": "generating"})

@app.get("/articles/status/{job_id}")
async def api_article_status(job_id: str):
    """Check article generation status."""
    job = article_jobs.get(job_id)
    if not job:
        return JSONResponse({"status": "not_found"}, status_code=404)
    return JSONResponse(job)

@app.get("/articles/list")
async def api_list_articles():
    """List all generated articles from wiki."""
    from pathlib import Path
    wiki_root = Path(r"C:\knowledge-base") / "wiki"
    articles = []
    for md_file in wiki_root.rglob("articles/*.md"):
        try:
            content = md_file.read_text(encoding="utf-8", errors="ignore")
            # Parse frontmatter
            if content.startswith("---"):
                end = content.index("---", 3)
                fm = content[3:end].strip()
                meta = {}
                for line in fm.split("\n"):
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip().strip('"')
                articles.append({
                    "file": str(md_file.relative_to(wiki_root)),
                    "title": meta.get("title", md_file.stem),
                    "category": meta.get("category", ""),
                    "seo_score": int(meta.get("seo_score", 0)),
                    "generated_at": meta.get("generated_at", ""),
                    "slug": meta.get("slug", "")
                })
        except Exception:
            continue
    articles.sort(key=lambda x: x.get("generated_at", ""), reverse=True)
    return JSONResponse({"articles": articles})