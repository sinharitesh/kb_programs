"""
One-time backfill: imports facts from verified_facts.md files into DuckDB.
Run this once from C:\Users\sinha\git\kb_programs
"""
import re, duckdb
from pathlib import Path

KB_ROOT = Path(r"C:\knowledge-base")
con = duckdb.connect(str(KB_ROOT / "kb.duckdb"))
imported = 0
skipped = 0

for fact_file in (KB_ROOT / "wiki").rglob("verified_facts.md"):
    content = fact_file.read_text(encoding="utf-8", errors="ignore")
    wiki_files = [f for f in fact_file.parent.glob("*.md") if f.name != "verified_facts.md"]
    url_id = None
    for wf in wiki_files:
        wc = wf.read_text(encoding="utf-8", errors="ignore")
        url_match = re.search(r'^url:\s*(\S+)', wc, re.MULTILINE)
        if url_match:
            row = con.execute("SELECT id FROM url_registry WHERE url=?", [url_match.group(1)]).fetchone()
            if row:
                url_id = row[0]
                break
    if not url_id:
        print(f"SKIP (no URL match): {fact_file}")
        skipped += 1
        continue
    facts = re.findall(r'^- (?:\[\d+\] )?(.+?)$', content, re.MULTILINE)
    existing = set(r[0] for r in con.execute("SELECT fact FROM facts WHERE url_id=?", [url_id]).fetchall())
    for fact in facts:
        fact = fact.strip()
        if fact and fact not in existing and not fact.startswith('#'):
            next_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM facts").fetchone()[0]
            con.execute("INSERT INTO facts (id, url_id, fact, verified) VALUES (?,?,?,?)",
                        [next_id, url_id, fact, False])
            imported += 1

con.close()
print(f"\n✅ Done! Imported: {imported} facts | Skipped (no URL match): {skipped}")
input("\nPress Enter to close...")
