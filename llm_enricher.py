# llm_enricher.py
import json
import httpx
import re
import os
import logging
from pathlib import Path
from datetime import datetime

KB_ROOT = Path(r"C:\knowledge-base")
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL = "qwen2.5:14b"
MAX_CHUNK_WORDS = 2000

# Configure logging
LOG_LEVEL = logging.DEBUG if os.environ.get('KB_DEBUG') else logging.INFO
logger = logging.getLogger("kb.enricher")
logger.setLevel(LOG_LEVEL)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s | %(name)s | %(levelname)s | %(message)s', '%H:%M:%S'))
    logger.addHandler(handler)

def log(msg):
    logger.info(msg)
# ── Prompt Templates ───────────────────────────────────────────
CHUNK_PROMPT = """You are a knowledge base assistant. Extract structured information from this section of an article.

Title: {title}
URL: {url}
Section: {section_title}
Content: {text}

Return ONLY a valid JSON object:
{{
  "summary": "2-3 sentence summary of this section",
  "facts": ["fact 1", "fact 2", "fact 3"],
  "tags": ["tag1", "tag2"],
  "entities": ["entity1", "entity2"],
  "cross_refs": ["possible/category/path1"]
}}
"""

QUESTIONS_PROMPT = """You are a research assistant. Based on this article, generate 5-7 thoughtful questions that a reader might ask.

Title: {title}
Summary: {summary}
Tags: {tags}

Return ONLY a valid JSON object:
{{
  "questions": ["question 1", "question 2", "question 3", "question 4", "question 5"]
}}
"""

FACT_SCORING_PROMPT = """You are an editor evaluating article facts. For each fact, assign an "interest score" (1-10) based on how surprising, useful, or engaging it is for a general reader. Also, for each fact, indicate whether it is likely verifiable via Wikipedia (yes/no) and provide a Wikipedia search phrase.

Article title: {title}

Facts:
{facts_json}

Return ONLY a valid JSON object:
{{
  "scored_facts": [
    {{"fact": "the fact text", "interest_score": 7, "needs_verification": true, "wiki_search": "search phrase for Wikipedia"}},
    ...
  ]
}}
"""


MERGE_PROMPT = """You are a knowledge base assistant. Below are enriched sections of an article.
Merge them into one coherent, comprehensive knowledge base entry.

Title: {title}
Sections: {sections}

Return ONLY a valid JSON object:
{{
  "summary": "comprehensive 4-5 sentence summary of the whole article",
  "facts": ["fact 1", "fact 2", ...],
  "tags": ["tag1", "tag2", ...],
  "entities": ["entity1", "entity2", ...],
  "cross_refs": ["path1", "path2", ...],
  "suggested_path": "top_category/sub/specific",
  "quality_score": 8
}}
"""

def calc_quality(enriched, scrape_result, verified):
    "Calculate quality score (0-10) from measurable metrics"
    word_count = scrape_result.get("word_count", 0)
    facts = len(enriched.get("facts", []))
    tags = len(enriched.get("tags", []))
    cross_refs = len(enriched.get("cross_refs", []))
    entities = len(enriched.get("entities", []))
    verified_count = sum(1 for v in verified.values() if v.get("verified"))
    wscore = 0 if word_count < 200 else 0.5 if word_count < 500 else 1 if word_count < 2000 else 2
    fscore = min(3, facts)
    tscore = min(2, tags)
    cscore = min(1, cross_refs)
    vscore = min(1, verified_count)
    escore = min(1, entities)
    return wscore + fscore + tscore + cscore + vscore + escore

# ── Ollama Caller ──────────────────────────────────────────────
def call_ollama(prompt: str) -> str:
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2}
    }
    try:
        r = httpx.post(OLLAMA_URL, json=payload, timeout=180)
        r.raise_for_status()
        return r.json()["response"]
    except Exception as e:
        return json.dumps({"error": str(e)})

# ── JSON Extractor ─────────────────────────────────────────────
def extract_json(raw: str) -> dict:
    raw = raw.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        return {"error": "Failed to parse LLM output", "raw": raw[:200]}

# ── Chunker ────────────────────────────────────────────────────
def chunk_text(text: str) -> list[dict]:
    """Split by headings first, fall back to fixed word chunks."""
    chunks = []

    # Try splitting by markdown-style or caps headings
    heading_pattern = re.compile(r'\n([A-Z][^\n]{3,60})\n', re.MULTILINE)
    parts = heading_pattern.split(text)

    if len(parts) > 2:
        # Odd indices are headings, even are content
        i = 0
        current_title = "Introduction"
        current_text = parts[0].strip()
        while i < len(parts):
            if i % 2 == 1:  # heading
                if current_text:
                    chunks.append({"title": current_title, "text": current_text})
                current_title = parts[i].strip()
                current_text = parts[i+1].strip() if i+1 < len(parts) else ""
                i += 2
            else:
                i += 1
        if current_text:
            chunks.append({"title": current_title, "text": current_text})
    else:
        # No headings — split into fixed word chunks
        words = text.split()
        for i in range(0, len(words), MAX_CHUNK_WORDS):
            chunk_words = words[i:i + MAX_CHUNK_WORDS]
            chunks.append({
                "title": f"Section {i // MAX_CHUNK_WORDS + 1}",
                "text": " ".join(chunk_words)
            })

    # Further split any chunk that's still too large
    final_chunks = []
    for chunk in chunks:
        words = chunk["text"].split()
        if len(words) > MAX_CHUNK_WORDS:
            for i in range(0, len(words), MAX_CHUNK_WORDS):
                final_chunks.append({
                    "title": f"{chunk['title']} (part {i // MAX_CHUNK_WORDS + 1})",
                    "text": " ".join(words[i:i + MAX_CHUNK_WORDS])
                })
        else:
            final_chunks.append(chunk)

    return final_chunks

# ── Fact Verifier ──────────────────────────────────────────────
def verify_via_wikipedia(entity: str) -> dict:
    try:
        import urllib.parse
        encoded = urllib.parse.quote(entity.replace(" ", "_")[:100])
        r = httpx.get("https://en.wikipedia.org/api/rest_v1/page/summary/" + encoded,
            headers={"User-Agent": "KBManager/1.0"}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return {
                "verified": True,
                "wiki_summary": data.get("extract", "")[:300],
                "wiki_url": data.get("content_urls", {}).get("desktop", {}).get("page", "")
            }
    except Exception:
        pass
    return {"verified": False}

# ── Main Enrichment ────────────────────────────────────────────
def score_and_verify_facts(title, facts):
    "Score each fact for interest (1-10) and verify key facts against Wikipedia"
    if not facts: return {}
    try:
        prompt = FACT_SCORING_PROMPT.format(title=title, facts_json=json.dumps(facts[:30]))
        raw = call_ollama(prompt)
        parsed = extract_json(raw)
        scored = {f["fact"]: {"interest_score": f.get("interest_score", 5), "needs_verification": f.get("needs_verification", False), "wiki_search": f.get("wiki_search", "")} for f in parsed.get("scored_facts", [])}
        # Verify top-5 facts that need verification
        for fact_text, info in scored.items():
            if info["needs_verification"] and info["wiki_search"]:
                v = verify_via_wikipedia(info["wiki_search"])
                info["verified"] = v["verified"]
                info["wiki_summary"] = v.get("wiki_summary", "")
                info["wiki_url"] = v.get("wiki_url", "")
            else: info["verified"] = False
        log(f"[Scoring] Scored {len(scored)} facts")
        return scored
    except Exception as e:
        log(f"[Scoring] Error: {e}")
        return {}

def enrich(scrape_result: dict, user_category: str = None) -> dict:
    if scrape_result.get("status") != "ok":
        return {"error": "Cannot enrich failed scrape", "details": scrape_result}

    title = scrape_result["title"]
    url = scrape_result["url"]
    full_text = scrape_result.get("full_text") or scrape_result.get("text", "")

    chunks = chunk_text(full_text)
    log(f"[LLM] Processing {len(chunks)} chunks for: {title}")

    section_results = []
    for i, chunk in enumerate(chunks):
        log(f"[LLM] Chunk {i+1}/{len(chunks)}: {chunk['title']}")
        prompt = CHUNK_PROMPT.format(
            title=title,
            url=url,
            section_title=chunk["title"],
            text=chunk["text"]
        )
        raw = call_ollama(prompt)
        parsed = extract_json(raw)
        if "error" not in parsed:
            parsed["section_title"] = chunk["title"]
            section_results.append(parsed)

    if not section_results:
        return {"error": "All chunks failed to parse"}

    # Merge via LLM
    log(f"[LLM] Merging {len(section_results)} sections...")
    merge_prompt = MERGE_PROMPT.format(
        title=title,
        sections=json.dumps(section_results, indent=2)[:8000]  # cap merge input
    )
    merged = extract_json(call_ollama(merge_prompt))

    final_path = user_category or merged.get("suggested_path", "uncategorized")

    # Verify top entities
    verified = {}
    for entity in merged.get("entities", [])[:3]:
        verified[entity] = verify_via_wikipedia(entity)

    # Score facts for interest and verify against Wikipedia
    facts = merged.get("facts", [])
    fact_ratings = score_and_verify_facts(title, facts)

    return {
        "title": title, "url": url, "category_path": final_path,
        "summary": merged.get("summary", ""), "facts": facts,
        "tags": merged.get("tags", []), "entities": merged.get("entities", []),
        "cross_refs": merged.get("cross_refs", []),
        "quality_score": calc_quality(merged, scrape_result, verified),
        "sections": section_results, "images": scrape_result.get("images", []),
        "raw_file": scrape_result.get("raw_file", ""),
        "verified_entities": verified, "fact_ratings": fact_ratings,
        "enriched_at": datetime.now().isoformat()
    }

# ── Wiki Writer ────────────────────────────────────────────────
def write_wiki_file(enriched: dict):
    path = KB_ROOT / "wiki" / enriched["category_path"]
    path.mkdir(parents=True, exist_ok=True)

    slug = enriched["title"].lower()
    slug = re.sub(r'[^a-z0-9]+', '_', slug)[:40].strip('_')
    filepath = path / f"{slug}.md"

    img_links = "\n".join([f"![img](images/{i})" for i in enriched["images"]]) or "No images"

    wiki_notes = ""
    for entity, info in enriched["verified_entities"].items():
        if info.get("verified"):
            wiki_notes += f"\n- **{entity}**: {info['wiki_summary'][:150]}... [wiki]({info['wiki_url']})"

    # Build sections detail
    sections_md = ""
    for s in enriched.get("sections", []):
        sections_md += f"\n### {s.get('section_title', 'Section')}\n"
        sections_md += s.get("summary", "") + "\n"
        for fact in s.get("facts", []):
            sections_md += f"- {fact}\n"
            
    title_safe = enriched['title'].replace('"', '\\"')
    content = f"""---
title: "{title_safe}"
url: {enriched['url']}
path: {enriched['category_path']}
tags: {json.dumps(enriched['tags'])}
cross_refs: {json.dumps(enriched['cross_refs'])}
quality_score: {enriched['quality_score']}
enriched_at: {enriched['enriched_at']}
---

# {enriched['title']}

## Summary
{enriched['summary']}

## Key Facts
{chr(10).join(['- ' + f for f in enriched['facts']])}

## Detailed Sections
{sections_md}

## Verified Entities
{wiki_notes or 'None verified'}

## Images
{img_links}

## Source
[{enriched['url']}]({enriched['url']})
"""
    filepath.write_text(content, encoding="utf-8")
    log(f"[Wiki] Written: {filepath}")
    return str(filepath)

# ── Index Updater ──────────────────────────────────────────────
def update_indexes(enriched: dict):
    top_level = enriched["category_path"].split("/")[0]
    cat_index = KB_ROOT / "index" / f"{top_level}_index.md"
    if not cat_index.exists():
        cat_index.write_text(f"# {top_level.title()} Index\n\n| Title | Path | Tags | Cross-refs |\n|---|---|---|---|\n")
    entry = f"| {enriched['title']} | {enriched['category_path']} | {', '.join(enriched['tags'])} | {', '.join(enriched['cross_refs'])} |\n"
    with open(cat_index, "a", encoding="utf-8") as f:
        f.write(entry)

    master = KB_ROOT / "index" / "master_crossref.md"
    xref_entry = f"\n## {enriched['title']}\n- Path: `{enriched['category_path']}`\n"
    xref_entry += "- Cross-refs:\n" + "\n".join([f"  - `{x}`" for x in enriched["cross_refs"]])
    with open(master, "a", encoding="utf-8") as f:
        f.write(xref_entry + "\n")

# ── Full Pipeline ──────────────────────────────────────────────
def _save_enrichment_questions(title, category, questions):
    from db import get_con
    con = get_con()
    keyphrase = title[:100]
    # Delete old questions for this keyphrase, then insert fresh ones
    con.execute("DELETE FROM questions_research WHERE keyphrase = ? AND source = 'llm_enriched'", [keyphrase])
    rows = [(category, keyphrase, q, "llm_enriched") for q in questions]
    con.executemany("INSERT INTO questions_research (category, keyphrase, question, source) VALUES (?,?,?,?)", rows)
    con.close()
    log(f"[Questions] Saved {len(rows)} from enrichment: {title[:60]}")

def process_scrape_result(scrape_result: dict, user_category: str = None, discovery_source: str = None):
    enriched = enrich(scrape_result, user_category)
    if "error" in enriched:
        print(f"[Error] {enriched}")
        return enriched
    wiki_path = write_wiki_file(enriched)
    
    # Generate & save research questions
    try:
        q_prompt = QUESTIONS_PROMPT.format(
            title=enriched.get("title", ""),
            summary=enriched.get("summary", ""),
            tags=", ".join(enriched.get("tags", []))
        )
        log(f"[Questions] Generating for: {enriched.get('title', '')[:60]}")
        q_raw = call_ollama(q_prompt)
        log(f"[Questions] LLM response: {q_raw[:120]}")
        q_parsed = extract_json(q_raw)
        log(f"[Questions] Parsed: {q_parsed}")
        if "questions" in q_parsed and q_parsed["questions"]:
            _save_enrichment_questions(enriched.get("title", ""), user_category or enriched.get("category_path", "uncategorized"), q_parsed["questions"])
        else:
            log(f"[Questions] No questions in response: {q_parsed}")
    except Exception as e:
        log(f"[Questions] Error: {e}")
    from db import register_url
    url_id = register_url(
        url=scrape_result["url"],
        title=enriched.get("title", ""),
        domain=scrape_result.get("domain", ""),
        quality_score=enriched.get("quality_score", 0),
        word_count=scrape_result.get("word_count", 0),
        raw_file=scrape_result.get("raw_file", ""),
        status="enriched"
    )
    # Fetch DDG web facts
    ddg_facts = fetch_ddg_facts(enriched.get("title", ""))
    # Fetch Wikipedia + Google facts
    wiki_facts = _fetch_wikipedia_facts(enriched.get("title", ""))
    google_facts = _fetch_google_facts(enriched.get("title", ""))
    # Save facts to DuckDB (LLM-extracted + DDG web facts + Wikipedia-verified entities + Google)
    from db import save_facts_to_db
    save_facts_to_db(
        url_id=url_id,
        facts=enriched.get("facts", []),
        verified_entities=enriched.get("verified_entities", {}),
        ddg_facts=ddg_facts,
        wiki_facts=wiki_facts,
        google_facts=google_facts,
        fact_ratings=enriched.get("fact_ratings", {}),
        discovery_source=discovery_source
    )
    update_indexes(enriched)
    return {**enriched, "wiki_file": wiki_path, "url_id": url_id}


def fetch_ddg_facts(query: str, max_results: int = 10) -> list:
    """Fetch web snippets from DuckDuckGo as verified facts."""
    try:
        import re
        def clean_text(text):
            text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
            text = re.sub(r'([0-9])([A-Z])', r'\1 \2', text)
            return re.sub(r' +', ' ', text).strip()
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return [{"snippet": clean_text(r["body"]), "url": r["href"]} for r in results if r.get("body")]
    except Exception as e:
        log(f"[DDG] Error: {e}")
        return []

def verify_unverified_facts(batch_size=1000, sleep_between=30):
    "Verify unverified facts against Wikipedia search API — slow background service"
    import urllib.parse, time
    from db import get_con
    con = get_con(); facts = con.execute("""
        SELECT f.id, f.fact FROM facts f
        WHERE (f.verified = FALSE OR f.verified IS NULL) AND f.source = 'llm'
        AND (f.verification_source IS NULL OR f.verification_source = '')
        LIMIT ?
    """, [batch_size]).fetchall(); con.close()
    if not facts: return 0
    verified_count = 0
    for fid, fact_text in facts:
        try:
            if len(fact_text) < 20: continue
            search_phrase = fact_text.split('.')[0][:80].strip().rstrip(',')
            if len(search_phrase) < 10: continue
            sr = httpx.get("https://en.wikipedia.org/w/api.php",
                params={"action": "query", "list": "search", "srsearch": search_phrase, "format": "json", "srlimit": 1},
                headers={"User-Agent": "KBManager/1.0"}, timeout=15)
            if sr.status_code != 200: continue
            results = sr.json().get("query", {}).get("search", [])
            if not results: continue
            title = results[0]["title"]
            encoded = urllib.parse.quote(title.replace(" ", "_"))
            sm = httpx.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}",
                headers={"User-Agent": "KBManager/1.0"}, timeout=15)
            if sm.status_code == 200:
                con = get_con()
                con.execute("UPDATE facts SET verified=TRUE, verification_source='wikipedia', interest_score=interest_score+1 WHERE id=? AND interest_score<10", [fid])
                con.close()
                verified_count += 1
        except: pass
        time.sleep(sleep_between)
    if verified_count: log(f"[Verify] Verified {verified_count}/{len(facts)} facts via Wikipedia")
    return verified_count

def start_background_verifier():
    "Start a daemon thread that continuously verifies facts with 30s+ delays"
    import threading, time
    def verifier_loop():
        time.sleep(60)
        while True:
            try:
                remaining = verify_unverified_facts(1000, 35)
                if remaining == 0: time.sleep(600)  # No more facts, check in 10min
                else: time.sleep(60)
            except Exception as e: log(f"[Verifier] Error: {e}"); time.sleep(300)
    t = threading.Thread(target=verifier_loop, daemon=True); t.start()
    log("[Verifier] Background service started (35s delay, continuous)")
    return t

def _fetch_wikipedia_facts(query: str) -> list:
    "Fetch Wikipedia page snippets as verified facts"
    import httpx, re
    try:
        r = httpx.get("https://en.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": 5},
            headers={"User-Agent": "KBManager/1.0"}, timeout=10)
        titles = [i["title"] for i in r.json()["query"]["search"]]
        facts = []
        for t in titles[:3]:
            rr = httpx.get("https://en.wikipedia.org/api/rest_v1/page/summary/" + t.replace(" ", "_"), timeout=10)
            if rr.status_code == 200:
                d = rr.json()
                facts.append({"snippet": d.get("extract", "")[:500], "url": d.get("content_urls", {}).get("desktop", {}).get("page", "")})
        return facts
    except Exception as e:
        log(f"[Wiki-facts] Error: {e}")
        return []

def _fetch_google_facts(query: str) -> list:
    "Fetch Google search snippets as facts"
    import httpx
    try:
        r = httpx.get("https://suggestqueries.google.com/complete/search?client=firefox&q=" + query.replace(" ", "+"), timeout=10)
        suggestions = r.json()[1][:10] if len(r.json()) > 1 else []
        return [{"snippet": s, "url": f"https://www.google.com/search?q={s.replace(' ', '+')}"} for s in suggestions]
    except Exception as e:
        log(f"[Google-facts] Error: {e}")
        return []




