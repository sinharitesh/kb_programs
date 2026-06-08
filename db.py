# db.py
import duckdb
from pathlib import Path
from datetime import datetime

KB_ROOT = Path(r"C:\knowledge-base")

def get_con():
    return duckdb.connect(str(KB_ROOT / "kb.duckdb"))

def is_url_registered(url: str) -> dict | None:
    con = get_con()
    result = con.execute(
        "SELECT id, status, quality_score, refresh_requested FROM url_registry WHERE url = ?",
        [url]
    ).fetchone()
    con.close()
    if result:
        return {"id": result[0], "status": result[1], "quality_score": result[2], "refresh_requested": result[3]}
    return None

def register_url(url: str, title: str, domain: str, quality_score: int,
                 word_count: int, raw_file: str, status: str, discovery_source: str = None):
    # Note: discovery_source column needs to be added to url_registry table via migration
    con = get_con()
    now = datetime.now()
    existing = con.execute("SELECT id FROM url_registry WHERE url = ?", [url]).fetchone()
    if existing:
        con.execute("""
            UPDATE url_registry SET title=?, last_downloaded=?, quality_score=?,
            word_count=?, raw_file=?, status=?, refresh_requested=FALSE WHERE url=?
        """, [title, now, quality_score, word_count, raw_file, status, url])
        url_id = existing[0]
    else:
        next_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM url_registry").fetchone()[0]
        con.execute("""
            INSERT INTO url_registry (id, url, title, domain, first_downloaded, last_downloaded,
            quality_score, word_count, raw_file, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [next_id, url, title, domain, now, now, quality_score, word_count, raw_file, status])
        url_id = next_id

    con.close()
    return url_id

def assign_path(url_id: int, path: str):
    con = get_con()
    existing = con.execute(
        "SELECT 1 FROM url_paths WHERE url_id=? AND path=?", [url_id, path]
    ).fetchone()
    if not existing:
        con.execute(
            "INSERT INTO url_paths VALUES (?, ?, ?)",
            [url_id, path, datetime.now()]
        )
    con.close()
    
    
import shutil

def move_url_path(url_id: int, old_path: str, new_path: str):
    con = get_con()
    # Update DB
    con.execute("""
        UPDATE url_paths SET path=?, assigned_at=?
        WHERE url_id=? AND path=?
    """, [new_path, datetime.now(), url_id, old_path])
    con.close()

    # Move files on disk
    for base in ["wiki", "raw"]:
        old_dir = KB_ROOT / base / old_path
        new_dir = KB_ROOT / base / new_path
        if old_dir.exists():
            new_dir.mkdir(parents=True, exist_ok=True)
            for f in old_dir.iterdir():
                shutil.move(str(f), str(new_dir / f.name))



def migrate_facts_add_source():
    """Add source column to facts table if it doesn't exist."""
    con = get_con()
    try:
        # Check if source column exists
        con.execute("SELECT source FROM facts LIMIT 1")
    except:
        # Column doesn't exist, add it
        con.execute("ALTER TABLE facts ADD COLUMN source VARCHAR(20) DEFAULT 'llm'")
        con.commit()
        print("Migrated facts table: added source column")
    con.close()


def migrate_facts_add_discovery_source():
    """Add discovery_source column to facts table to track origin."""
    con = get_con()
    try:
        con.execute("SELECT discovery_source FROM facts LIMIT 1")
    except:
        con.execute("ALTER TABLE facts ADD COLUMN discovery_source VARCHAR(20)")
        con.commit()
        print("Migrated facts table: added discovery_source column")
    con.close()

def _ensure_fact_scoring_columns(con):
    "Add interest_score and verification_source columns to facts if missing"
    try: con.execute("SELECT interest_score FROM facts LIMIT 1")
    except: con.execute("ALTER TABLE facts ADD COLUMN interest_score INTEGER DEFAULT 5")
    try: con.execute("SELECT verification_source FROM facts LIMIT 1")
    except: con.execute("ALTER TABLE facts ADD COLUMN verification_source TEXT")


def save_facts_to_db(url_id: int, facts: list[str], verified_entities: dict = None, ddg_facts: list = None, wiki_facts: list = None, google_facts: list = None, fact_ratings: dict = None, discovery_source: str = None):
    """Save LLM-extracted, DDG, Wiki, Google facts, and Wikipedia-verified entities to DuckDB."""
    con = get_con()
    migrate_facts_add_source(); migrate_facts_add_discovery_source()
    _ensure_fact_scoring_columns(con)
    next_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM facts").fetchone()[0]
    # LLM-extracted facts — scored for interest and optionally verified
    for fact in facts:
        if fact and fact.strip():
            rating = (fact_ratings or {}).get(fact, {})
            interest = rating.get("interest_score", 5)
            ver_src = "wikipedia" if rating.get("verified") else (rating.get("wiki_search")[:80] if rating.get("wiki_search") else None)
            con.execute("INSERT INTO facts (id, url_id, fact, verified, source, discovery_source, interest_score, verification_source) VALUES (?,?,?,?,?,?,?,?)",
                [next_id, url_id, fact.strip(), rating.get("verified", False), 'llm', discovery_source, interest, ver_src]); next_id += 1
    # DDG web-sourced facts
    if ddg_facts:
        for item in ddg_facts:
            snippet = item.get("snippet", "").strip(); source_url = item.get("url", "")
            if snippet: con.execute("INSERT INTO facts (id, url_id, fact, verified, source, discovery_source, interest_score, verification_source) VALUES (?,?,?,?,?,?,?,?)",
                [next_id, url_id, f"{snippet} [src: {source_url}]"[:500] if source_url else snippet[:500], True, 'ddg_facts', discovery_source, 4, 'ddg']); next_id += 1
    if wiki_facts:
        for item in wiki_facts:
            snippet = item.get("snippet", "").strip(); source_url = item.get("url", "")
            if snippet: con.execute("INSERT INTO facts (id, url_id, fact, verified, source, discovery_source, interest_score, verification_source) VALUES (?,?,?,?,?,?,?,?)",
                [next_id, url_id, f"{snippet} [src: {source_url}]"[:500] if source_url else snippet[:500], True, 'wikipedia', discovery_source, 6, 'wikipedia']); next_id += 1
    if google_facts:
        for item in google_facts:
            snippet = item.get("snippet", "").strip(); source_url = item.get("url", "")
            if snippet: con.execute("INSERT INTO facts (id, url_id, fact, verified, source, discovery_source, interest_score, verification_source) VALUES (?,?,?,?,?,?,?,?)",
                [next_id, url_id, f"{snippet} [src: {source_url}]"[:500] if source_url else snippet[:500], True, 'google', discovery_source, 3, 'google']); next_id += 1
    if verified_entities:
        for entity, info in verified_entities.items():
            if info.get("verified") and info.get("wiki_summary"):
                con.execute("INSERT INTO facts (id, url_id, fact, verified, source, discovery_source, interest_score, verification_source) VALUES (?,?,?,?,?,?,?,?)",
                    [next_id, url_id, f"{entity}: {info['wiki_summary'][:300]}"[:500], True, 'wikipedia', discovery_source, 5, 'wikipedia']); next_id += 1
    con.close()




# ── Index Management Helpers ───────────────────────────────────

def get_index_summary():
    """Return counts from all tables for dashboard."""
    con = get_con()
    summary = {}
    summary["urls_total"]    = con.execute("SELECT COUNT(*) FROM url_registry").fetchone()[0]
    summary["urls_enriched"] = con.execute("SELECT COUNT(*) FROM url_registry WHERE status='enriched'").fetchone()[0]
    summary["urls_scraped"]  = con.execute("SELECT COUNT(*) FROM url_registry WHERE status='scraped'").fetchone()[0]
    summary["urls_failed"]   = con.execute("SELECT COUNT(*) FROM url_registry WHERE status='failed'").fetchone()[0]
    summary["facts_total"]   = con.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    summary["facts_verified"]= con.execute("SELECT COUNT(*) FROM facts WHERE verified=TRUE").fetchone()[0]
    summary["keywords_total"]= con.execute("SELECT COUNT(*) FROM keyword_intelligence").fetchone()[0]
    summary["keywords_topics"]= con.execute("SELECT COUNT(DISTINCT topic) FROM keyword_intelligence").fetchone()[0]
    summary["questions_total"]= con.execute("SELECT COUNT(*) FROM questions_research").fetchone()[0]
    summary["questions_categories"]= con.execute("SELECT COUNT(DISTINCT category) FROM questions_research").fetchone()[0]
    summary["categories_total"]= con.execute("SELECT COUNT(DISTINCT path) FROM url_paths").fetchone()[0]
    con.close()
    return summary

def get_index_by_category():
    """Return URL counts grouped by category."""
    con = get_con()
    rows = con.execute("""
        SELECT p.path, COUNT(DISTINCT p.url_id) as url_count,
               AVG(r.quality_score) as avg_quality
        FROM url_paths p
        JOIN url_registry r ON r.id = p.url_id
        GROUP BY p.path ORDER BY url_count DESC
    """).fetchall()
    con.close()
    return [{"path": r[0], "url_count": r[1], "avg_quality": round(r[2] or 0, 1)} for r in rows]

def get_keywords_index(topic_filter=""):
    """Return keyword intelligence grouped by topic and source."""
    con = get_con()
    q = "SELECT topic, source, COUNT(*) as cnt, MAX(analyzed_at) as last_run FROM keyword_intelligence"
    params = []
    if topic_filter:
        q += " WHERE topic ILIKE ?"
        params.append(f"%{topic_filter}%")
    q += " GROUP BY topic, source ORDER BY topic, source"
    rows = con.execute(q, params).fetchall()
    con.close()
    return [{"topic": r[0], "source": r[1], "count": r[2], "last_run": str(r[3])} for r in rows]

def get_facts_index(url_id=None):
    """Return facts with their source URLs."""
    con = get_con()
    if url_id:
        rows = con.execute("""
            SELECT f.id, f.fact, f.verified, r.title, r.url
            FROM facts f JOIN url_registry r ON r.id = f.url_id
            WHERE f.url_id = ? ORDER BY f.id DESC
        """, [url_id]).fetchall()
    else:
        rows = con.execute("""
            SELECT f.id, f.fact, f.verified, r.title, r.url
            FROM facts f JOIN url_registry r ON r.id = f.url_id
            ORDER BY f.id DESC LIMIT 200
        """).fetchall()
    con.close()
    return [{"id": r[0], "fact": r[1], "verified": r[2], "source_title": r[3], "source_url": r[4]} for r in rows]

def get_questions_index(category_filter=""):
    """Return questions grouped by category and keyphrase."""
    con = get_con()
    q = "SELECT category, keyphrase, COUNT(*) as cnt, MAX(analyzed_at) as last_run FROM questions_research"
    params = []
    if category_filter:
        q += " WHERE category ILIKE ?"
        params.append(f"%{category_filter}%")
    q += " GROUP BY category, keyphrase ORDER BY category, keyphrase"
    rows = con.execute(q, params).fetchall()
    con.close()
    return [{"category": r[0], "keyphrase": r[1], "count": r[2], "last_run": str(r[3])} for r in rows]

def search_index(query: str):
    """Unified search across facts, keywords, questions, and URLs."""
    con = get_con()
    q = f"%{query}%"
    results = {"urls": [], "facts": [], "keywords": [], "questions": []}

    rows = con.execute("""
        SELECT id, title, url, domain, quality_score FROM url_registry
        WHERE title ILIKE ? OR url ILIKE ? LIMIT 20
    """, [q, q]).fetchall()
    results["urls"] = [{"id": r[0], "title": r[1], "url": r[2], "domain": r[3], "quality_score": r[4]} for r in rows]

    rows = con.execute("""
        SELECT f.fact, f.verified, r.title FROM facts f
        JOIN url_registry r ON r.id = f.url_id
        WHERE f.fact ILIKE ? LIMIT 20
    """, [q]).fetchall()
    results["facts"] = [{"fact": r[0], "verified": r[1], "source": r[2]} for r in rows]

    rows = con.execute("""
        SELECT topic, source, keyword, score FROM keyword_intelligence
        WHERE keyword ILIKE ? OR topic ILIKE ? LIMIT 20
    """, [q, q]).fetchall()
    results["keywords"] = [{"topic": r[0], "source": r[1], "keyword": r[2], "score": r[3]} for r in rows]

    rows = con.execute("""
        SELECT category, keyphrase, question, source FROM questions_research
        WHERE question ILIKE ? OR keyphrase ILIKE ? LIMIT 20
    """, [q, q]).fetchall()
    results["questions"] = [{"category": r[0], "keyphrase": r[1], "question": r[2], "source": r[3]} for r in rows]

    con.close()
    return results

def export_index_data(table: str):
    """Export full table as list of dicts."""
    con = get_con()
    allowed = {"url_registry", "facts", "keyword_intelligence", "questions_research", "url_paths"}
    if table not in allowed:
        con.close()
        return []
    rows = con.execute(f"SELECT * FROM {table}").fetchall()
    cols = [d[0] for d in con.execute(f"DESCRIBE {table}").fetchall()]
    con.close()
    return [dict(zip(cols, r)) for r in rows]

# ── Delete / Cleanup Functions ─────────────────────────────────

def delete_fact(fact_id: int):
    con = get_con()
    con.execute("DELETE FROM facts WHERE id = ?", [fact_id])
    con.close()

def delete_facts_bulk(fact_ids: list[int]):
    con = get_con()
    con.execute(f"DELETE FROM facts WHERE id IN ({','.join(['?']*len(fact_ids))})", fact_ids)
    con.close()

def delete_facts_by_url(url_id: int):
    con = get_con()
    con.execute("DELETE FROM facts WHERE url_id = ?", [url_id])
    con.close()

def delete_keyword(keyword_id: int):
    con = get_con()
    con.execute("DELETE FROM keyword_intelligence WHERE id = ?", [keyword_id])
    con.close()

def delete_keywords_by_topic(topic: str):
    con = get_con()
    con.execute("DELETE FROM keyword_intelligence WHERE topic = ?", [topic])
    con.close()

def delete_keywords_bulk(keyword_ids: list[int]):
    con = get_con()
    con.execute(f"DELETE FROM keyword_intelligence WHERE id IN ({','.join(['?']*len(keyword_ids))})", keyword_ids)
    con.close()

def delete_question(question_id: int):
    con = get_con()
    con.execute("DELETE FROM questions_research WHERE id = ?", [question_id])
    con.close()

def delete_questions_by_category(category: str):
    con = get_con()
    con.execute("DELETE FROM questions_research WHERE category = ?", [category])
    con.close()

def delete_questions_bulk(question_ids: list[int]):
    con = get_con()
    con.execute(f"DELETE FROM questions_research WHERE id IN ({','.join(['?']*len(question_ids))})", question_ids)
    con.close()

def delete_url(url_id: int):
    """Delete URL and all associated facts, paths from DB."""
    con = get_con()
    con.execute("DELETE FROM facts WHERE url_id = ?", [url_id])
    con.execute("DELETE FROM url_paths WHERE url_id = ?", [url_id])
    con.execute("DELETE FROM url_registry WHERE id = ?", [url_id])
    con.close()

def delete_urls_below_quality(min_score: int):
    """Delete all URLs (and their facts/paths) with quality_score below threshold."""
    con = get_con()
    ids = [r[0] for r in con.execute(
        "SELECT id FROM url_registry WHERE quality_score < ?", [min_score]).fetchall()]
    if ids:
        ph = ','.join(['?']*len(ids))
        con.execute(f"DELETE FROM facts WHERE url_id IN ({ph})", ids)
        con.execute(f"DELETE FROM url_paths WHERE url_id IN ({ph})", ids)
        con.execute(f"DELETE FROM url_registry WHERE id IN ({ph})", ids)
    con.close()
    return len(ids)

def cleanup_orphans():
    """Remove facts/paths with no matching url_id in url_registry."""
    con = get_con()
    facts_del = con.execute("""
        DELETE FROM facts WHERE url_id NOT IN (SELECT id FROM url_registry)
    """).rowcount
    paths_del = con.execute("""
        DELETE FROM url_paths WHERE url_id NOT IN (SELECT id FROM url_registry)
    """).rowcount
    con.close()
    return {"orphan_facts_deleted": facts_del, "orphan_paths_deleted": paths_del}

def deduplicate_facts():
    "Remove duplicate facts (same fact text for the same url_id)"
    con = get_con()
    deleted = con.execute("""
        DELETE FROM facts WHERE id NOT IN (
            SELECT MIN(id) FROM facts GROUP BY fact, url_id
        )
    """).rowcount
    con.close()
    return deleted

def deduplicate_questions():
    "Remove duplicate questions (same question text for the same keyphrase)"
    con = get_con()
    deleted = con.execute("""
        DELETE FROM questions_research WHERE id NOT IN (
            SELECT MIN(id) FROM questions_research GROUP BY question, keyphrase
        )
    """).rowcount
    con.close()
    return deleted

def deduplicate_synthesized_keywords():
    "Remove duplicate synthesized keywords (same keyword+job_id)"
    con = get_con()
    deleted = con.execute("""
        DELETE FROM synthesized_keywords WHERE rowid NOT IN (
            SELECT MIN(rowid) FROM synthesized_keywords GROUP BY keyword, job_id
        )
    """).rowcount
    con.close()
    return deleted

def deduplicate_urls():
    "Remove duplicate URLs keeping the oldest entry"
    con = get_con()
    deleted = con.execute("""
        DELETE FROM url_registry WHERE id NOT IN (
            SELECT MIN(id) FROM url_registry GROUP BY url
        )
    """).rowcount
    con.close()
    return deleted

def deduplicate_keyword_intelligence():
    "Remove duplicates from keyword_intelligence (same keyword+source+notes)"
    con = get_con()
    deleted = con.execute("""
        DELETE FROM keyword_intelligence WHERE rowid NOT IN (
            SELECT MIN(rowid) FROM keyword_intelligence GROUP BY keyword, source, notes
        )
    """).rowcount
    con.close()
    return deleted

def deduplicate_all():
    "Run all deduplication and return counts"
    return dict(
        facts=deduplicate_facts(),
        questions=deduplicate_questions(),
        synthesized_keywords=deduplicate_synthesized_keywords(),
        keyword_intelligence=deduplicate_keyword_intelligence(),
        urls=deduplicate_urls(),
        orphans=cleanup_orphans()
    )

def get_facts_for_explorer(verified: str = "all", search: str = "", source: str = ""):
    """Return facts with source info, category, keywords, and interest scores."""
    con = get_con()
    _ensure_fact_scoring_columns(con)
    sql = """
        SELECT f.id, f.fact, f.verified, f.source, r.id as url_id, r.title, r.url, r.domain,
               COALESCE(p.path, 'uncategorized') as category,
               (SELECT GROUP_CONCAT(DISTINCT ki.keyword, ', ') FROM keyword_intelligence ki WHERE ki.notes = r.url) as keywords,
               COALESCE(f.interest_score, 5) as interest_score,
               COALESCE(f.verification_source, '') as verification_source
        FROM facts f
        JOIN url_registry r ON r.id = f.url_id
        LEFT JOIN url_paths p ON p.url_id = r.id
        WHERE 1=1
    """
    params = []
    if verified == "yes": sql += " AND f.verified = TRUE"; 
    elif verified == "no": sql += " AND f.verified = FALSE"; 
    if search: sql += " AND f.fact ILIKE ?"; params.append(f"%{search}%")
    if source: sql += " AND f.source = ?"; params.append(source)
    sql += " ORDER BY f.verified DESC, f.id DESC LIMIT 500"
    rows = con.execute(sql, params).fetchall()
    con.close()
    return [{"id": r[0], "fact": r[1], "verified": r[2], "source": r[3],
             "url_id": r[4], "source_title": r[5], "source_url": r[6], "domain": r[7],
             "category": r[8], "keywords": r[9], "interest_score": r[10], "verification_source": r[11]} for r in rows]

# ── Synthesized Keywords Persistence ──────────────────────────
def save_synthesized_keyword(keyword, suggested, category, urls, facts_count, job_id):
    "Save synthesized keyword results to DB for future article generation"
    import json
    con = get_con()
    con.execute("""
        INSERT OR REPLACE INTO synthesized_keywords
            (keyword, suggested, category, urls, facts_count, job_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, [keyword, json.dumps(suggested), category, json.dumps(urls), facts_count, job_id])
    con.close()

def get_synthesized_keywords(keyword_search="", category="", limit=100):
    "Retrieve saved synthesized keywords for article generation"
    import json
    con = get_con()
    where = ["1=1"]
    params = []
    if keyword_search: where.append("keyword ILIKE ?"); params.append(f"%{keyword_search}%")
    if category: where.append("category = ?"); params.append(category)
    where_sql = " AND ".join(where)
    rows = con.execute(f"""
        SELECT keyword, suggested, category, urls, facts_count, job_id, created_at
        FROM synthesized_keywords WHERE {where_sql}
        ORDER BY created_at DESC LIMIT ?
    """, params + [limit]).fetchall()
    con.close()
    return [dict(
        keyword=r[0], suggested=json.loads(r[1]), category=r[2],
        urls=json.loads(r[3]), facts_count=r[4], job_id=r[5],
        created_at=r[6].isoformat() if r[6] else None
    ) for r in rows]

def get_synthesized_keywords_for_topic(topic, limit=20):
    "Get keywords most relevant to a topic for article generation context"
    import json, re
    con = get_con()
    # Split topic into individual meaningful words and search broadly
    words = [w.strip() for w in re.split(r'[\s,;]+', topic) if len(w.strip()) > 2]
    where_sqls = ["keyword ILIKE ?", "category ILIKE ?"]
    params = []
    for w in words: where_sqls.append("keyword ILIKE ?"); params.append(f"%{w}%")
    where_sql = " OR ".join(where_sqls)
    rows = con.execute(f"""
        SELECT keyword, suggested, category, urls, facts_count, created_at
        FROM synthesized_keywords
        WHERE {where_sql}
        ORDER BY created_at DESC LIMIT ?
    """, [f"%{topic}%", f"%{topic}%"] + params + [limit]).fetchall()
    con.close()
    return [dict(
        keyword=r[0], suggested=json.loads(r[1]), category=r[2],
        urls=json.loads(r[3]), facts_count=r[4], created_at=r[5].isoformat() if r[5] else None
    ) for r in rows]