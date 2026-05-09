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
                 word_count: int, raw_file: str, status: str):
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
