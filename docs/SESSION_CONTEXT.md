# KB Manager - Session Context

## Project
- **Repo:** https://github.com/sinharitesh/kb_programs (branch: master)
- **Local (Windows):** C:\Users\sinha\git\kb_programs
- **DB:** C:\knowledge-base\kb.duckdb
- **LLM:** Ollama qwen2.5:14b at http://localhost:11434
- **Token file (solveit):** /app/data/.gh_token
- **Repo clone (solveit):** /app/data/kb_programs

## Git Push Template
import subprocess from pathlib import Path repo = "/app/data/kb_programs" token = Path("/app/data/.gh_token").read_text().strip() subprocess.run(['git', 'remote', 'set-url', 'origin', f'https://{token}@github.com/sinharitesh/kb_programs.git'], cwd=repo) subprocess.run(['git', 'add', '-A'], cwd=repo) subprocess.run(['git', 'commit', '-m', 'your message'], cwd=repo) subprocess.run(['git', 'push'], cwd=repo)



## Categories
shiva | krishna | hanumana | durga
Config: C:\knowledge-base\config\categories.json

## Database Schema
- url_registry: id, url, title, domain, quality_score, word_count, raw_file, status, refresh_requested
- url_paths: url_id, path, assigned_at
- facts: id, url_id, fact, verified, source (llm|ddg_facts|wikipedia|reddit|google)
- keyword_intelligence: id, topic, category, source, keyword, score, notes, analyzed_at
- questions_research: id, category, keyphrase, question, source, notes, analyzed_at

## UI Tabs
1. Ingest URL - Scrape + enrich pipeline with duplicate detection
2. Queue - Job status monitor
3. URL Manager - Browse/filter/delete (quality score, category search)
4. Browse Wiki - View/edit enriched markdown files
5. Keyword Finder - Multi-source research with category dropdown
6. Facts Explorer - Filter by source/verified/search, bulk delete
7. Questions Research - Card-based category selector
8. Index Management - Summary, search, export, cleanup

## Completed Features
- URL ingestion with duplicate detection
- LLM enrichment (chunked, merged)
- Facts storage with source tracking
- Questions with proper categories (not general)
- Facts Explorer source filter
- URL Manager quality filter + sort + category search
- Questions card-based UI with search
- Bulk delete for all tables
- Orphan cleanup
- Comprehensive docs in /docs folder

## Pending Features
- Quality Dashboard (#5) - Rank and delete low quality URLs
- Blog Draft Generator (#6) - Generate blog drafts from KB facts/keywords
- Run fix_general_questions.py locally to fix 88 remaining general questions

## Recent Commits
- 1c648c5 docs: add comprehensive documentation
- d01fee2 feat: Facts Explorer source filter complete
- 1160036 feat: card-based category selector for Questions
- e59418a fix: semantic keyword mappings (Sunder Kand->hanumana, Trishul->shiva)
- b2078af fix: Questions ILIKE + URL Manager score filter
