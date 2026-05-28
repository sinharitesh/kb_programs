# llm_enricher.py
import json
import httpx
import re
import os
import logging
from pathlib import Path
from datetime import datetime

KB_ROOT = Path(r"C:\knowledge-base")
OLLAMA_URL = "http://localhost:11434/api/generate"
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
        r = httpx.get(
            "https://en.wikipedia.org/api/rest_v1/page/summary/" + entity.replace(" ", "_"),
            timeout=10
        )
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

    return {
        "title": title,
        "url": url,
        "category_path": final_path,
        "summary": merged.get("summary", ""),
        "facts": merged.get("facts", []),
        "tags": merged.get("tags", []),
        "entities": merged.get("entities", []),
        "cross_refs": merged.get("cross_refs", []),
        "quality_score": calc_quality(merged, scrape_result, verified),
        "sections": section_results,  # keep all section details
        "images": scrape_result.get("images", []),
        "raw_file": scrape_result.get("raw_file", ""),
        "verified_entities": verified,
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
        q_raw = call_ollama(q_prompt)
        q_parsed = extract_json(q_raw)
        if "questions" in q_parsed and q_parsed["questions"]:
            _save_enrichment_questions(enriched.get("title", ""), user_category or enriched.get("category_path", "uncategorized"), q_parsed["questions"])
    except Exception: pass
    
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
    # Save facts to DuckDB (LLM-extracted + DDG web facts + Wikipedia-verified entities)
    from db import save_facts_to_db
    save_facts_to_db(
        url_id=url_id,
        facts=enriched.get("facts", []),
        verified_entities=enriched.get("verified_entities", {}),
        ddg_facts=ddg_facts,
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




