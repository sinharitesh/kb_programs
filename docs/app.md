# app.py - FastAPI Web Application

## Overview
Main FastAPI application serving all HTTP routes for the KB Manager UI.

## Key Routes

### Home
- `GET /` - Serves the main HTML UI (index.html) with categories context

### Ingestion Pipeline
- `POST /ingest` - Enqueue URL for scraping. Returns `job_id` or `SKIP:url_id` if already downloaded
- `GET /queue` - Returns all job statuses
- `GET /status/{job_id}` - Poll single job
- `POST /enrich/{job_id}` - Trigger LLM enrichment after user review

### Categories & Domains
- `GET /categories` - List all configured categories
- `POST /categories/add` - Add new category (creates wiki/raw folders)
- `POST /categories/remove` - Remove category
- `GET /domains` - List whitelist/blocklist domains
- `POST /domains/add` - Add domain to whitelist or blocklist
- `POST /domains/remove` - Remove domain

### URL Manager
- `GET /urls` - List URLs with filters: `search`, `path`, `cat_search`, `score_op`, `score_val`, `sort_quality`
- `POST /urls/assign-path` - Assign additional category path to URL
- `POST /urls/move-path` - Move URL from one category to another
- `POST /urls/request-refresh` - Flag URL for re-download
- `DELETE /urls/{url_id}` - Delete URL and cascade delete facts/paths
- `POST /urls/delete-below-quality` - Bulk delete URLs below quality threshold

### Wiki Browser
- `GET /wiki` - List all wiki markdown files
- `GET /wiki/file` - Read a specific wiki file content
- `POST /wiki/save` - Save edited wiki file content

### Keywords
- `POST /keywords/analyze` - Run keyword intelligence (Google/Wikipedia/Reddit/DDG) for topic+category
- `GET /keywords/explore` - Browse saved keyword intelligence data
- `DELETE /keywords/{id}` - Delete single keyword entry
- `DELETE /keywords/topic/{topic}` - Delete all keywords for a topic

### Facts Explorer
- `GET /facts/explorer` - Browse facts with filters: `verified` (all/yes/no), `search`, `source` (llm/ddg_facts/wikipedia/reddit/google)
- `DELETE /facts/{fact_id}` - Delete single fact
- `POST /facts/delete-bulk` - Bulk delete list of fact IDs

### Questions Research
- `GET /questions` - List questions with filters: `category[]`, `keyphrase`, `source`
- `DELETE /questions/{id}` - Delete single question
- `DELETE /questions/category/{cat}` - Delete all questions in category
- `POST /questions/delete-bulk` - Bulk delete questions

### Index Management
- `GET /index/summary` - Row counts for all tables
- `GET /index/categories` - URL counts grouped by category
- `GET /index/keywords` - Keywords grouped by topic/source
- `GET /index/facts` - Facts list with source URLs
- `GET /index/questions` - Questions grouped by category/keyphrase
- `GET /index/search` - Unified search across all tables
- `GET /index/export` - Export table as JSON or CSV download
- `POST /index/rebuild` - Rebuild markdown category index files from wiki
- `POST /index/import-facts` - Backfill facts from verified_facts.md files
- `POST /db/cleanup-orphans` - Remove orphaned facts/paths

## Important Notes
- Categories are loaded from `C:\knowledge-base\config\categories.json`
- KB root is hardcoded to `C:\knowledge-base`
- Enrichment is user-confirmed, not automatic
