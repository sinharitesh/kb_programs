# article_generator.py
# Article generation with KB-aware context gathering

import os, re, json, logging
from pathlib import Path
from datetime import datetime
from db import get_con
from image_search import get_article_images

KB_ROOT = Path(r"C:\knowledge-base")
OLLAMA_MODEL = "gemma3:12b"
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"

logger = logging.getLogger("article_gen")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", "%H:%M:%S"))
    logger.addHandler(ch)


# ── Context Gathering ─────────────────────────────────────────────────────────

def gather_facts(category: str = "", search_phrases: list = None, limit: int = 7) -> list:
    """Gather top facts from DuckDB, filtered by category and/or text search."""
    con = get_con()
    results = []

    # By text search, scoped to the category
    if search_phrases:
        for phrase in search_phrases:
            phrase = phrase.strip()
            if not phrase or len(phrase) < 3: continue
            rows = con.execute("""
                SELECT f.id, f.fact, f.verified, f.source,
                       r.title as source_title, r.url as source_url
                FROM facts f
                JOIN url_registry r ON r.id = f.url_id
                JOIN url_paths p ON p.url_id = r.id
                WHERE f.fact ILIKE ? AND p.path ILIKE ?
                ORDER BY f.verified DESC, f.id DESC
                LIMIT ?
            """, [f"%{phrase}%", f"%{category}%", max(3, limit // 2)]).fetchall()
            for r in rows:
                if not any(x["id"] == r[0] for x in results):
                    results.append({
                        "id": r[0], "fact": r[1], "verified": r[2], "source": r[3],
                        "source_title": r[4], "source_url": r[5], "match": f"search:{phrase}"
                    })

    con.close()
    # Return top N, verified first
    results.sort(key=lambda x: (not x["verified"], x["id"]))
    logger.info(f"Gathered {len(results[:limit])} facts (category={category}, phrases={search_phrases})")
    return results[:limit]


def gather_questions(category: str = "", search_phrases: list = None, limit: int = 7) -> list:
    """Gather top questions from DuckDB, filtered by text search and category."""
    con = get_con()
    results = []

    if search_phrases:
        for phrase in search_phrases:
            phrase = phrase.strip()
            if not phrase or len(phrase) < 3: continue
            rows = con.execute("""
                SELECT id, category, keyphrase, question, source
                FROM questions_research
                WHERE question ILIKE ? OR keyphrase ILIKE ?
                ORDER BY id DESC
                LIMIT ?
            """, [f"%{phrase}%", f"%{phrase}%", max(5, limit)]).fetchall()
            for r in rows:
                if not any(x["id"] == r[0] for x in results):
                    results.append({
                        "id": r[0], "category": r[1], "keyphrase": r[2],
                        "question": r[3], "source": r[4], "match": f"search:{phrase}"
                    })

    # Also include category-matched questions
    if category:
        rows = con.execute("""
            SELECT id, category, keyphrase, question, source
            FROM questions_research
            WHERE category ILIKE ?
            ORDER BY id DESC
            LIMIT ?
        """, [f"%{category}%", limit]).fetchall()
        for r in rows:
            if not any(x["id"] == r[0] for x in results):
                results.append({
                    "id": r[0], "category": r[1], "keyphrase": r[2],
                    "question": r[3], "source": r[4], "match": "category"
                })

    con.close()
    logger.info(f"Gathered {len(results[:limit])} questions (category={category}, phrases={search_phrases})")
    return results[:limit]


def find_answer(question: str, category: str = "") -> dict:
    """Search KB wiki .md files and facts for an answer to a question."""
    answer = None
    source = None

    # 1. Search wiki .md files by category first
    wiki_root = KB_ROOT / "wiki"
    search_dirs = []
    if category:
        cat_dir = wiki_root / category
        if cat_dir.exists():
            search_dirs.append(cat_dir)
    search_dirs.append(wiki_root)  # fallback: search all

    keywords = [w.lower() for w in question.split() if len(w) > 3]

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for md_file in search_dir.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8", errors="ignore")
                content_lower = content.lower()
                # Score by keyword matches
                score = sum(1 for kw in keywords if kw in content_lower)
                if score >= 2:  # at least 2 keywords match
                    # Extract relevant paragraph
                    for para in content.split("\n\n"):
                        para_lower = para.lower()
                        para_score = sum(1 for kw in keywords if kw in para_lower)
                        if para_score >= 2 and len(para.strip()) > 30:
                            answer = para.strip()[:300]
                            source = str(md_file.relative_to(wiki_root))
                            break
                if answer:
                    break
            except Exception:
                continue
        if answer:
            break

    # 2. Search verified facts if no wiki answer found
    if not answer:
        con = get_con()
        rows = con.execute("""
            SELECT f.fact FROM facts f
            WHERE f.verified = TRUE AND f.fact ILIKE ?
            ORDER BY f.id DESC LIMIT 1
        """, [f"%{' '.join(keywords[:3])}%"]).fetchall()
        con.close()
        if rows:
            answer = rows[0][0]
            source = "verified_fact"

    return {
        "answer": answer,
        "source": source,
        "found": answer is not None
    }


def gather_wiki_context(category: str, search_phrases: list = None, max_chars: int = 3000) -> str:
    """Read relevant .md files from KB wiki folder for article context."""
    wiki_root = KB_ROOT / "wiki"
    context_parts = []
    total_chars = 0

    # Search category folder first, then fall back to all wiki
    search_dirs = []
    if category:
        cat_dir = wiki_root / category
        if cat_dir.exists(): search_dirs.append(cat_dir)
    search_dirs.append(wiki_root)  # cross-reference from other categories

    keywords = []
    if search_phrases:
        keywords = [w.lower().strip() for phrase in search_phrases for w in phrase.split() if len(w) > 3]

    for search_dir in search_dirs:
        for md_file in sorted(search_dir.rglob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8", errors="ignore")
                # Score relevance
                content_lower = content.lower()
                score = sum(1 for kw in keywords if kw in content_lower) if keywords else 1
                if score >= 1:
                    excerpt = content[:500]
                    context_parts.append({
                        "file": str(md_file.relative_to(wiki_root)),
                        "excerpt": excerpt,
                        "score": score
                    })
                    total_chars += len(excerpt)
                    if total_chars >= max_chars:
                        break
            except Exception:
                continue

    # Sort by relevance score
    context_parts.sort(key=lambda x: -x["score"])
    logger.info(f"Gathered {len(context_parts)} wiki excerpts ({total_chars} chars)")
    return context_parts


def gather_all_context(idea: str, category: str, search_phrases: list = None,
                       fact_limit: int = 7, question_limit: int = 7) -> dict:
    """Master context gathering function."""
    # Build search phrases from idea + user phrases for relevance filtering
    idea_words = [w.strip() for w in idea.split() if len(w) > 3]
    all_phrases = list(idea_words)
    if search_phrases: all_phrases.extend(search_phrases)
    # Gather facts
    facts = gather_facts(category, all_phrases, fact_limit)
    # If too few facts found, broaden search across all categories
    if len(facts) < 3:
        broad_facts = gather_facts("", all_phrases, 5)
        for f in broad_facts:
            if not any(x["id"] == f["id"] for x in facts): facts.append(f)
        logger.info(f"Broadened fact search: {len(facts)} total (was {len([f for f in facts if f.get('match','').startswith('search:')])})")
    # Score by keyword match density and sort top first
    for f in facts: f["score"] = sum(1 for w in idea_words if w.lower() in f["fact"].lower())
    facts.sort(key=lambda f: f["score"], reverse=True)

    # Gather questions and find answers
    questions = gather_questions(category, all_phrases, question_limit)
    # Score by keyword match density and sort top first
    for q in questions: q["score"] = sum(1 for w in idea_words if w.lower() in q["question"].lower())
    questions.sort(key=lambda q: q["score"], reverse=True)
    for q in questions:
        ans = find_answer(q["question"], category)
        q["answer"] = ans["answer"]
    
    # Gather wiki context
    wiki_context = gather_wiki_context(category, search_phrases)
    
    return {"idea": idea, "facts": facts, "questions": questions, "wiki_context": wiki_context}


# ── LLM Helpers ───────────────────────────────────────────────────────────────

def ollama_generate(prompt, temperature=0.0):
    import requests, re
    response = requests.post(OLLAMA_URL,
        json={'model': OLLAMA_MODEL, 'prompt': prompt, 'stream': False, 'options': {'temperature': temperature}}, timeout=300)
    response.raise_for_status()
    text = response.json()['response']
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    return text


def clean_article(text):
    """Remove LLM preamble/postamble from generated text."""
    text = re.sub(r'^(here\s+is|here\'s|below\s+is|sure[,!]?)[^\n]*\n+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\n+(note[:\s]|let me know|i hope|feel free)[^\n]*$', '', text, flags=re.IGNORECASE)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


# ── Prompt Building ───────────────────────────────────────────────────────────

def build_facts_block(facts):
    """Format selected facts for the article prompt, sorting by interest score. Includes source URLs for natural linking."""
    sorted_facts = sorted(facts, key=lambda f: f.get("interest_score", 5), reverse=True)
    lines = []
    for i, f in enumerate(sorted_facts, 1):
        source = f.get("source_title", f.get("source", "source"))
        source_url = f.get("source_url", "")
        url_hint = f" — link: {source_url}" if source_url else ""
        marker = "★ MOST INTERESTING FACT" if i == 1 else f"Fact {i}"
        lines.append(f"[{marker} — via {source}{url_hint} | Interest ⭐{f.get('interest_score',5)}/10]\n{f['fact'].strip()}\n")
    return "\n".join(lines)


def build_questions_block(questions):
    """Format selected questions for the article prompt."""
    answered = []
    unanswered = []
    for q in questions:
        if q.get("answer_found"):
            answered.append(f"Q: {q['question']}\nA: {q['answer']}")
        else:
            unanswered.append(f"Q: {q['question']} (Answer not available — weave into article)")
    
    parts = []
    if answered:
        parts.append("━━━ FAQ (include as FAQ section) ━━━\n" + "\n\n".join(answered))
    if unanswered:
        parts.append("━━━ UNANSWERED QUESTIONS (weave into article body for SEO) ━━━\n" + "\n".join(unanswered))
    return "\n\n".join(parts)


def build_wiki_context_block(wiki_context):
    """Format wiki excerpts for the article prompt."""
    if not wiki_context:
        return "(No KB context available)"
    parts = []
    for ctx in wiki_context[:5]:
        parts.append(f"[From: {ctx['file']}]\n{ctx['excerpt'][:300]}")
    return "\n\n".join(parts)


def build_article_prompt(context: dict, settings: dict) -> str:
    """Build the full article generation prompt from gathered context and user settings."""
    facts_block = build_facts_block(context.get("selected_facts", context["facts"]))
    questions_block = build_questions_block(context.get("selected_questions", context["questions"]))
    wiki_block = build_wiki_context_block(context["wiki_context"])

    title = settings.get("title", context["idea"])
    keywords = settings.get("keywords", context["idea"])
    focus_keyphrase = settings.get("focus_keyphrase", keywords)
    tone = settings.get("tone", "informative and engaging")
    word_count = settings.get("word_count", 1200)
    language = settings.get("language", "en")
    content_type = settings.get("content_type", "Blog Post")

    LANG_MAP = {
        'hi': 'Hindi (Devanagari script). Use English for headings only.',
        'en': 'English', 'es': 'Spanish', 'fr': 'French', 'de': 'German',
    }
    lang_instruction = LANG_MAP.get(language, 'English')

    return f"""You are a world-class content writer known for writing articles that feel alive — the kind readers cannot stop halfway through.

Write a compelling {content_type} in {lang_instruction}.

━━━ ARTICLE BRIEF ━━━
Title            : {title}
Keywords         : {keywords}
Focus Keyphrase  : {focus_keyphrase}
Tone             : {tone}
Word Count       : approximately {word_count} words

━━━ KNOWLEDGE BASE CONTEXT ━━━
{wiki_block}

━━━ VERIFIED FACTS (weave naturally — do NOT list dryly) ━━━
{facts_block}

━━━ QUESTIONS & ANSWERS ━━━
{questions_block}

━━━ CROSS-REFERENCE INSTRUCTIONS ━━━
1. Link facts to KB context when they align — e.g., if a fact says "Thawe temple is 400 years old" and KB context mentions Thawe, connect them naturally.
2. Weave answers from Q&A into sections where a reader would ask those questions.
3. If the KB context and facts disagree, note the ambiguity or give the most authoritative version.

━━━ STRICT WRITING RULES ━━━

1. FIRST SENTENCE = the ★ MOST INTERESTING FACT above. State it directly. No setup. No narrative preamble. No "Imagine..." or "As I sit...". Just the fact, delivered like a headline.

2. FIRST PARAGRAPH (3-4 sentences): Expand the ★ fact with context from KB or other facts. Make the reader think "I need to know more." End with a question or teaser that compels reading further.

3. FACTS — Never dump as bullet points:
   - Weave into narrative like a storyteller
   - Each fact gets its own section or paragraph
   - Follow each with "So what?" — why does it matter?

4. STRUCTURE:
   ## [Attention-grabbing title that hints at the ★ fact]
   Lead paragraph expanding the ★ fact
   ## [Deeper context — how/why/what]  
   ## [Lesser-known angle or surprising detail]
   ## Did You Know?  ← 3 punchy facts as short standalone paragraphs (NOT bullets)
   ## Frequently Asked Questions  ← Include answered questions as FAQ
   ## [Modern relevance or takeaway]
   ## [Conclusion — tie back to the opening fact]

5. LANGUAGE:
   - First paragraph: punchy, declarative, zero fluff
   - Mix short punchy sentences with richer descriptive ones
   - Rhetorical questions throughout  
   - Vivid imagery and analogies
   - NO filler: "In conclusion", "It is important to note", "In today's world"
   - NO flowery intros: "As I sit...", "Imagine a world...", "In the annals of..."

6. SEO — use keyphrase and keywords naturally, include unanswered questions in article body

7. END with meta description as blockquote:
   > **Meta:** [max 155 chars]

Output ONLY the article markdown — nothing before the first heading.
"""


def build_seo_prompt(article_md: str, keywords: str) -> str:
    """Build Yoast-style SEO analysis prompt."""
    return f"""Analyze this article for Yoast-style SEO. Target keyword: "{keywords}"

Article excerpt:
{article_md[:1500]}

Return ONLY a valid JSON object with these keys:
- seo_title (max 60 chars, click-worthy)
- meta_description (max 155 chars, compelling)
- focus_keyphrases (list of 3-5 terms)
- central_keyword (single most important keyword)
- tags (list of 5-8 tags)
- slug (URL-friendly, lowercase, hyphens only)
- readability (Easy / Medium / Hard)
- seo_score (integer 0-100)
- og_title (OpenGraph title, max 60 chars)
- og_description (OpenGraph description, max 200 chars)
- twitter_title (Twitter card title, max 70 chars)
- twitter_description (Twitter card description, max 200 chars)
- canonical_url (suggested canonical path)
- schema_type (Schema.org type: Article, BlogPosting, FAQPage, etc.)
- keyphrase_density (percentage as string, e.g. "1.5%")
- internal_links (list of 3-5 suggested internal link anchor texts)
- outbound_links (list of 2-3 suggested external reference topics)

Do not include any explanation, markdown, or code fences. Just the raw JSON object."""


# ── Article Generation Pipeline ───────────────────────────────────────────────

def generate_article(context: dict, settings: dict) -> dict:
    """Full article generation pipeline: prompt → LLM → clean → SEO."""
    logger.info("Starting article generation...")

    # Build and call LLM
    prompt = build_article_prompt(context, settings)
    logger.info(f"Prompt built ({len(prompt)} chars), calling Ollama...")
    
    raw_article = ollama_generate(prompt, temperature=0.0)
    article_md = clean_article(raw_article)
    logger.info(f"Generated ~{len(article_md.split())} words")

    # SEO analysis
    logger.info("Running SEO analysis...")
    keywords = settings.get("keywords", context["idea"])
    seo_prompt = build_seo_prompt(article_md, keywords)
    seo_data = {}
    try:
        raw_seo = ollama_generate(seo_prompt, temperature=0.0)
        cleaned = raw_seo.strip()
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s*```$', '', cleaned)
        match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if match:
            seo_data = json.loads(match.group())
            logger.info(f"SEO Score: {seo_data.get('seo_score', 'N/A')}")
    except Exception as e:
        logger.warning(f"SEO analysis error: {e}")

    # ── Enrich article with verified fact URLs ──
    try:
        from db import get_con
        focus_kw = seo_data.get('focus_keyphrase') or settings.get('focus_keyphrase', '') or context.get('idea', '')
        con = get_con()
        rows = con.execute("""
            SELECT DISTINCT r.url, r.title FROM facts f
            JOIN url_registry r ON r.id = f.url_id
            WHERE f.verified = TRUE AND (f.fact ILIKE ? OR r.title ILIKE ?)
            LIMIT 10
        """, [f"%{focus_kw}%", f"%{focus_kw}%"]).fetchall()
        con.close()
        if rows:
            links = "\n".join([f"- [{r[1] or 'Verified source'}]({r[0]})" for r in rows[:3]])
            if "## References" not in article_md and "## Sources" not in article_md and "## Further Reading" not in article_md:
                article_md += f"\n\n## References\n\n{links}\n"
            logger.info(f"Enriched article with {min(3, len(rows))} reference links")
    except Exception as e:
        logger.warning(f"Link enrichment error: {e}")

    # Save to KB wiki
    save_path = save_article(article_md, context, settings, seo_data)
    slug = seo_data.get("slug", "")
    if not slug:
        slug = re.sub(r'[^a-z0-9]+', '-', context["idea"].lower())[:40].strip('-')

    return {
        "article_md": article_md,
        "word_count": len(article_md.split()),
        "seo": seo_data,
        "saved_to": save_path,
        "slug": slug,
        "generated_at": datetime.now().isoformat(),
        "generation_prompt": prompt,
        "settings": settings
    }

def _safe_text(item):
    "Extract text from any context item type"
    if not item: return ''
    if isinstance(item, str): return item[:200]
    return (item.get('fact') or item.get('question') or item.get('keyword') or item.get('text') or str(item))[:200]

def save_article(article_md: str, context: dict, settings: dict, seo_data: dict) -> str:
    """Save generated article to KB wiki folder."""
    category = context.get("category", "uncategorized")
    slug = seo_data.get("slug", "")
    if not slug:
        slug = re.sub(r'[^a-z0-9]+', '-', context["idea"].lower())[:40].strip('-')

    wiki_dir = KB_ROOT / "generated_articles" / category
    wiki_dir.mkdir(parents=True, exist_ok=True)
    filepath = wiki_dir / f"{slug}.md"

    # Add Yoast-style frontmatter
    frontmatter = f"""---
title: "{settings.get('title', context['idea'])}"
slug: {slug}
category: {category}
seo_score: {seo_data.get('seo_score', 0)}
generated_at: "{datetime.now().isoformat()}"
language: "{settings.get('language', 'en')}"
word_count: {len(article_md.split())}
focus_keyphrase: "{settings.get('focus_keyphrase', '')}"
tags: {json.dumps(seo_data.get('tags', []))}
seo_title: "{seo_data.get('seo_title', '')}"
meta_description: "{seo_data.get('meta_description', '')}"
central_keyword: "{seo_data.get('central_keyword', '')}"
focus_keyphrases: {json.dumps(seo_data.get('focus_keyphrases', []))}
og_title: "{seo_data.get('og_title', '')}"
og_description: "{seo_data.get('og_description', '')}"
twitter_title: "{seo_data.get('twitter_title', '')}"
twitter_description: "{seo_data.get('twitter_description', '')}"
canonical_url: "{seo_data.get('canonical_url', '')}"
schema_type: "{seo_data.get('schema_type', 'Article')}"
keyphrase_density: "{seo_data.get('keyphrase_density', '0%')}"
internal_links: {json.dumps(seo_data.get('internal_links', []))}
outbound_links: {json.dumps(seo_data.get('outbound_links', []))}
readability: "{seo_data.get('readability', 'Medium')}"
generated_at: {datetime.now().isoformat()}
context_facts: {json.dumps([_safe_text(f) for f in context.get('selected_facts', [])[:20]])}
context_questions: {json.dumps([_safe_text(q) for q in context.get('selected_questions', [])[:20]])}
context_synth_kw: {json.dumps([_safe_text(s) for s in context.get('selected_synthesized_keywords', [])[:20]])}
context_kw_intel: {json.dumps([_safe_text(k) for k in context.get('selected_keyword_intelligence', [])[:20]])}
---

"""
    filepath.write_text(frontmatter + article_md, encoding="utf-8")
    logger.info(f"Article saved: {filepath}")
    return str(filepath)