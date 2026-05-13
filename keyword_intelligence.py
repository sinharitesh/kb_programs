# keyword_intelligence.py
import httpx
import json
import os
import logging
from pathlib import Path
from db import get_con
from datetime import datetime
from ddgs import DDGS

KB_ROOT = Path(r"C:\knowledge-base")

# Configure logging
LOG_LEVEL = logging.DEBUG if os.environ.get('KB_DEBUG') else logging.INFO
logger = logging.getLogger("kb.keywords")
logger.setLevel(LOG_LEVEL)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s | %(name)s | %(levelname)s | %(message)s', '%H:%M:%S'))
    logger.addHandler(handler)

def log(msg):
    logger.info(msg)

import re

def clean_text(text: str) -> str:
    """Fix jumbled text from DDG snippets where spaces between HTML elements are lost.
    e.g. 'isHanumanTemple' -> 'is Hanuman Temple'
    """
    if not text:
        return text
    # Insert space before uppercase letters that follow lowercase letters (camelCase fix)
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    # Insert space before uppercase letters that follow digits
    text = re.sub(r'([0-9])([A-Z])', r'\1 \2', text)
    # Collapse multiple spaces
    text = re.sub(r' +', ' ', text)
    return text.strip()

def fetch_duckduckgo_facts(query: str, max_results: int = 10) -> list:
    try:
        with DDGS() as ddgs:
            results = [(r.get('body',''), r.get('href',''))
                       for r in ddgs.text(query + " interesting facts", max_results=max_results)]
        return [{"snippet": clean_text(body), "url": url} for body, url in results if body]
    except Exception as e:
        log(f"[DDG] Error: {e}")
        return []

def fetch_wikipedia_via_ddg(query: str) -> list:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(f"{query} site:wikipedia.org", max_results=5))
        return [r.get('title','') for r in results]
    except Exception as e:
        print(f"[Wiki-DDG] Error: {e}")
        return []


def google_suggestions(query: str) -> list:
    try:
        url = f"https://suggestqueries.google.com/complete/search?client=firefox&q={query}"
        r = httpx.get(url, timeout=10)
        r.encoding = 'utf-8'
        return r.json()[1][:10]
    except Exception as e:
        print(f"[Suggest] Error: {e}")
        return []


def wikipedia_related(query: str) -> list:
    try:
        r = httpx.get("https://en.wikipedia.org/w/api.php", 
            params={"action": "query", "list": "search",
                    "srsearch": query, "format": "json", "srlimit": 10},
            headers={"User-Agent": "KBManager/1.0 (knowledge-base-tool)"},
            timeout=10)
        return [i["title"] for i in r.json()["query"]["search"]]
    except Exception as e:
        print(f"[Wiki] Error: {e}")
        return []


def reddit_trending(query: str) -> list:
    try:
        r = httpx.get(f"https://www.reddit.com/search.json?q={query}&limit=10",
            headers={"User-Agent": "KBBot/1.0"}, timeout=10)
        posts = r.json()["data"]["children"]
        return [{"title": p["data"]["title"], "score": p["data"]["score"],
                 "url": p["data"]["url"]} for p in posts]
    except Exception as e:
        print(f"[Reddit] Error: {e}")
        return []

def run_keyword_intelligence(topic: str, category: str) -> dict:
    print(f"[KW] Running keyword intelligence for: {topic}")
    suggestions = google_suggestions(topic)
    wiki = wikipedia_related(topic)
    reddit = reddit_trending(topic)
    ddg_facts = fetch_duckduckgo_facts(topic)        # ← add this
    questions = fetch_questions(topic)
    save_questions(category, topic, questions)
    wiki_ddg = fetch_wikipedia_via_ddg(topic)        # ← add this

    ranked = []
    for i, s in enumerate(suggestions):
        ranked.append({"keyword": s, "source": "Google Suggest", "rank": i+1})
    for i, w in enumerate(wiki):
        ranked.append({"keyword": w, "source": "Wikipedia", "rank": i+1})
    for i, r in enumerate(reddit):
        ranked.append({"keyword": r["title"], "source": f"Reddit (⬆{r['score']})", "rank": i+1})
    for i, f in enumerate(ddg_facts):                # ← add this
        ranked.append({"keyword": f["snippet"][:80], "source": "DDG Facts", "rank": i+1})

    data = {
        "google_suggestions": suggestions,
        "wikipedia_related": wiki,
        "reddit_trending": reddit,
        "ddg_facts": ddg_facts,                      # ← add this
        "wiki_ddg": wiki_ddg                         # ← add this
    }

    save_to_duckdb(topic, category, data)
    return {**data, "topic": topic, "ranked": ranked}

    

def save_to_duckdb(topic: str, category: str, data: dict):
    con = get_con()
    analyzed_at = datetime.now().isoformat()
    rows = []
    
    # Google search URL for each suggestion
    for kw in data.get("google_suggestions", []):
        search_url = f"https://www.google.com/search?q={kw.replace(' ', '+')}"
        rows.append((topic, category, "google", kw, 0, search_url, analyzed_at))
    
    # Wikipedia article URL
    for w in data.get("wikipedia_related", []):
        article_url = f"https://en.wikipedia.org/wiki/{w.replace(' ', '_')}"
        rows.append((topic, category, "wikipedia", w, 0, article_url, analyzed_at))
    
    for r in data.get("reddit_trending", []):
        rows.append((topic, category, "reddit", r["title"], r["score"], r["url"], analyzed_at))
    
    for f in data.get("ddg_facts", []):
        rows.append((topic, category, "ddg_facts", f["snippet"][:200], 0, f["url"], analyzed_at))
    
    con.executemany("INSERT INTO keyword_intelligence (topic, category, source, keyword, score, notes, analyzed_at) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    con.close()
    print(f"[KW] Appended {len(rows)} rows to DuckDB")
    
    # Auto-queue discovered URLs for scraping with their discovery source
    from scraper import enqueue_scrape
    queued = 0
    
    # URLs are discovered but NOT auto-queued - user decides via manual selection
    discovered_urls = [(row[2], row[5]) for row in rows if row[5] and row[5].startswith("http")]
    if discovered_urls:
        print(f"[KW] Discovered {len(discovered_urls)} URLs (manual selection required)")
        # URLs stored in keyword_intelligence table for user to review and select
    path = KB_ROOT / "wiki" / category_path
    path.mkdir(parents=True, exist_ok=True)
    filepath = path / "keyword_intelligence.md"

    google_md = "\n".join([f"- {s}" for s in data["google_suggestions"]])
    wiki_md = "\n".join([f"- {w}" for w in data["wikipedia_related"]])
    reddit_md = "\n".join([f"- {r['title']} (⬆{r['score']})" for r in data["reddit_trending"]])
    ddg_md = "\n".join([
        f"- {f['snippet'][:120]}  \n  🔗 [{f['url']}]({f['url']})"
        for f in data.get("ddg_facts", [])
    ])

    content = f"""# 🔑 Keyword Intelligence: {topic}

    ## 🔍 Google Suggestions
    {google_md or 'None found'}

    ## 📖 Wikipedia Related
    {wiki_md or 'None found'}

    ## 💬 Reddit Trending
    {reddit_md or 'None found'}

    ## 🦆 DDG Facts
    {ddg_md or 'None found'}
    """
    filepath.write_text(content, encoding="utf-8")
    print(f"[KW] Saved: {filepath}")
    return str(filepath)


def fetch_questions(topic: str) -> list:
    prefixes = ["who is", "why did", "how to", "what is", "when did", "where is"]
    questions = []
    for prefix in prefixes:
        try:
            url = f"https://suggestqueries.google.com/complete/search?client=firefox&q={prefix}+{topic}"
            r = httpx.get(url, timeout=10)
            results = r.json()[1]
            questions.extend([{"question": q, "source": "google_suggest"} for q in results if topic.lower() in q.lower()])
        except Exception as e:
            print(f"[Questions] Error for '{prefix}': {e}")
    return questions


def save_questions(category: str, keyphrase: str, questions: list):
    if not questions:
        print("[Questions] No questions to save")
        return
    con = get_con()
    rows = [(category, keyphrase, q["question"], q["source"], None) for q in questions]
    con.executemany("INSERT INTO questions_research (category, keyphrase, question, source, notes) VALUES (?,?,?,?,?)", rows)
    con.close()
    print(f"[Questions] Saved {len(rows)} questions")

# Alias for backward compatibility
save_keyword_report = save_to_duckdb