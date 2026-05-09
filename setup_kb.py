import os
import json
import duckdb
from pathlib import Path

KB_ROOT = Path(r"C:\knowledge-base")
SRC_ROOT = Path(r"C:\Users\sinha\OneDrive\Documents\github\kb_programs")

INITIAL_CATEGORIES = [
    "mythology/indian/deity",
    "mythology/indian/epic",
    "mythology/greek",
    "travel/india/kerala",
    "religion/hindu/temples",
    "markets/stocks/company",
    "cooking/indian_dish/bihar",
]

def setup_folders():
    for cat in INITIAL_CATEGORIES:
        (KB_ROOT / "wiki" / Path(cat)).mkdir(parents=True, exist_ok=True)
        (KB_ROOT / "raw" / Path(cat)).mkdir(parents=True, exist_ok=True)

    for folder in ["index", "blog_drafts", "config"]:
        (KB_ROOT / folder).mkdir(parents=True, exist_ok=True)

def setup_config():
    categories_file = KB_ROOT / "config" / "categories.json"
    with open(categories_file, "w") as f:
        json.dump({"categories": INITIAL_CATEGORIES}, f, indent=2)

    domains_file = KB_ROOT / "config" / "domains.json"
    with open(domains_file, "w") as f:
        json.dump({
            "whitelist": ["wikipedia.org", "archive.org"],
            "blocklist": []
        }, f, indent=2)

def setup_indexes():
    top_level = set(cat.split("/")[0] for cat in INITIAL_CATEGORIES)
    for cat in top_level:
        idx = KB_ROOT / "index" / f"{cat}_index.md"
        idx.write_text(f"# {cat.title()} Index\n\n| Title | Path | Tags | Cross-refs |\n|---|---|---|---|\n")
    (KB_ROOT / "index" / "master_crossref.md").write_text("# Master Cross-Reference\n\n")

def setup_duckdb():
    con = duckdb.connect(str(KB_ROOT / "kb.duckdb"))
    con.execute("""
        CREATE TABLE IF NOT EXISTS url_registry (
            id INTEGER PRIMARY KEY,
            url TEXT UNIQUE,
            title TEXT,
            domain TEXT,
            first_downloaded TIMESTAMP,
            last_downloaded TIMESTAMP,
            quality_score INTEGER,
            word_count INTEGER,
            raw_file TEXT,
            status TEXT,
            refresh_requested BOOLEAN DEFAULT FALSE
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS url_paths (
            url_id INTEGER,
            path TEXT,
            assigned_at TIMESTAMP,
            FOREIGN KEY (url_id) REFERENCES url_registry(id)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY,
            url_id INTEGER,
            fact TEXT,
            verified BOOLEAN,
            FOREIGN KEY (url_id) REFERENCES url_registry(id)
        )
    """)
 
    con.execute("""
        CREATE TABLE IF NOT EXISTS keyword_intelligence (
            id INTEGER PRIMARY KEY,
            topic TEXT,
            category TEXT,
            source TEXT,        -- 'google', 'wikipedia', 'reddit'
            keyword TEXT,
            score INTEGER,      -- reddit upvotes or rank position
            notes TEXT,         -- extra info like source URL, context etc
            analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    con.execute("CREATE SEQUENCE IF NOT EXISTS seq_qr START 1")
    con.execute("""
        CREATE TABLE IF NOT EXISTS questions_research (
            id INTEGER DEFAULT nextval('seq_qr'),
            category TEXT,
            keyphrase TEXT,
            question TEXT,
            source TEXT,
            notes TEXT,
            analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.close()

if __name__ == "__main__":
    setup_folders()
    setup_config()
    setup_indexes()
    setup_duckdb()
    print("✅ Knowledge base initialized at", KB_ROOT)
