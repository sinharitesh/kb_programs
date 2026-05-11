# Knowledge Base (KB) Manager - Documentation

A local LLM-powered knowledge base for blogging research with a Google-style web UI.

## Architecture

KB Manager ├── FastAPI Backend (app.py) ├── DuckDB Database (kb.duckdb) ├── Local LLM via Ollama (qwen2.5:14b) └── Google-style HTML/JS UI (templates/index.html)



## Database Schema

| Table | Purpose |
|-------|---------|
| `url_registry` | Scraped URLs with title, domain, quality score, word count, status |
| `url_paths` | Category assignments for each URL |
| `facts` | Extracted facts with source and verified flag |
| `keyword_intelligence` | Google/Wikipedia/Reddit/DDG keyword data |
| `questions_research` | Related questions grouped by category and keyphrase |

## File Overview

| File | Purpose |
|------|---------|
| `app.py` | FastAPI routes - ingest, enrich, browse, search, index |
| `db.py` | All DuckDB operations - CRUD, migration, index queries |
| `scraper.py` | Background thread queue for URL scraping |
| `llm_enricher.py` | Ollama LLM enrichment + Wikipedia verification |
| `keyword_intelligence.py` | Keyword analysis from Google/Wikipedia/Reddit/DDG |
| `setup_kb.py` | One-time KB initialization |
| `templates/index.html` | Single-page Google-style web UI |

## Key Features

- **Ingest URLs** - Scrape, enrich with LLM, assign to categories
- **URL Manager** - Browse, filter by quality score and category
- **Wiki Browser** - View and edit enriched wiki markdown files
- **Keyword Finder** - Multi-source keyword research per category
- **Facts Explorer** - Browse facts filtered by source (LLM/DDG/Wikipedia/Reddit/Google)
- **Questions Research** - Card-based category selector for question browsing
- **Index Management** - Dashboard with summary, search, export, cleanup

## API Endpoints

### Ingestion
- `POST /ingest` - Queue URL for scraping
- `GET /status/{job_id}` - Poll scrape job status
- `POST /enrich/{job_id}` - Trigger LLM enrichment after review

### URLs
- `GET /urls` - List URLs with filters (search, category, quality score, sort)
- `POST /urls/assign-path` - Assign category to URL
- `DELETE /urls/{url_id}` - Delete URL and its facts/paths

### Facts
- `GET /facts/explorer` - Browse facts (filter by source, verified, search)
- `DELETE /facts/{fact_id}` - Delete single fact
- `POST /facts/delete-bulk` - Bulk delete facts

### Keywords
- `POST /keywords/analyze` - Run keyword intelligence for a topic
- `GET /keywords/explorer` - Browse saved keywords

### Questions
- `GET /questions` - List questions (filter by category, keyphrase, source)
- `DELETE /questions/{id}` - Delete question
- `DELETE /questions/category/{cat}` - Delete all in category

### Index Management
- `GET /index/summary` - Dashboard counts for all tables
- `GET /index/search` - Unified search across all tables
- `GET /index/export` - Export any table as JSON or CSV
- `POST /index/import-facts` - Backfill facts from wiki markdown files
- `POST /db/cleanup-orphans` - Remove orphaned records

## Facts Sources

Facts are stored with a source field:

| Source | Description | Verified |
|--------|-------------|---------|
| `llm` | LLM-extracted from article content | No |
| `ddg_facts` | DuckDuckGo web snippets | Yes |
| `wikipedia` | Wikipedia entity summaries | Yes |
| `reddit` | Reddit post titles | Yes |
| `google` | Google suggestions | Yes |

## Local LLM Setup

- **Model:** `qwen2.5:14b` via Ollama at `http://localhost:11434`
- **Machine:** 32GB RAM, 8GB VRAM
- **Fallback:** `nous-hermes2:10.7b`

## Configuration Files

Located in `C:\knowledge-base\config\`:
- `categories.json` - List of category paths (e.g. `mythology/indian/deity`)
- `domains.json` - Whitelist/blocklist of domains for scraping

## Pending Features

- **Quality Dashboard (#5)** - Rank and delete low quality URLs
- **Blog Draft Generator (#6)** - Generate blog drafts from KB facts and keywords
