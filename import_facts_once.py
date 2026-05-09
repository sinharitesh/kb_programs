# One-time backfill: extracts bullet-point facts from wiki .md files into DuckDB.
# Run this once from C:\Users\sinha\git\kb_programs
import re, duckdb
from pathlib import Path
from datetime import datetime

KB_ROOT = Path(r"C:\knowledge-base")
con = duckdb.connect(str(KB_ROOT / "kb.duckdb"))
imported = 0
skipped = 0

# Logging helper with timestamp
def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

for wiki_file in (KB_ROOT / "wiki").rglob("*.md"):
    if wiki_file.name == "verified_facts.md":
        continue

    content = wiki_file.read_text(encoding="utf-8", errors="ignore")

    # Extract URL from frontmatter
    url_match = re.search(r'^url:\s*(\S+)', content, re.MULTILINE)
    if not url_match:
        log(f"SKIP (no url in frontmatter): {wiki_file.name}")
        skipped += 1
        continue

    url = url_match.group(1)
    row = con.execute("SELECT id FROM url_registry WHERE url=?", [url]).fetchone()
    if not row:
        log(f"SKIP (url not in registry): {url}")
        skipped += 1
        continue

    url_id = row[0]

    # Extract body (after second --- in frontmatter)
    body = re.split(r'^---\s*$', content, flags=re.MULTILINE)
    body_text = body[2] if len(body) > 2 else body[-1]

    # Get bullet points as facts
    facts = re.findall(r'^[-*]\s+(.+)$', body_text, re.MULTILINE)

    existing = set(r[0] for r in con.execute("SELECT fact FROM facts WHERE url_id=?", [url_id]).fetchall())
    count = 0
    for fact in facts:
        fact = fact.strip()
        if fact and len(fact) > 20 and fact not in existing:
            next_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM facts").fetchone()[0]
            con.execute("INSERT INTO facts (id, url_id, fact, verified) VALUES (?,?,?,?)",
                        [next_id, url_id, fact, False])
            imported += 1
            count += 1
    log(f"Imported {count} facts from: {wiki_file.name}")

con.close()
log(f"Done! Imported: {imported} facts | Skipped: {skipped}")
input("\nPress Enter to close...")
