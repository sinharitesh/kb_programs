# scraper.py - Web Scraper

## Overview
Background thread-based URL scraping pipeline with a job queue.

## Key Functions

- `enqueue_scrape(url, category, keywords, force_refresh)` - Add URL to queue. Returns `SKIP:url_id` if already downloaded and force_refresh=False
- `get_job_status(job_id)` - Get current status of a scrape job
- `background_worker()` - Long-running thread that processes scrape queue

## Job Statuses
- `queued` - Waiting to be scraped
- `processing` - Currently being scraped
- `done` - Scraped successfully
- `rejected` - Failed domain check or junk detection
- `enriching` - LLM enrichment in progress

## Domain Filtering
- Checks against `domains.json` whitelist/blocklist
- Rejects URLs from blocklisted domains

## Junk Detection
- Filters out low-quality content (too short, login pages, etc.)
- Minimum word count threshold applied

## Storage
- Raw HTML saved to `C:\knowledge-base\raw\{category}\{filename}.txt`
- Results stored in `results_store` dict (in-memory)
