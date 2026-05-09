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

