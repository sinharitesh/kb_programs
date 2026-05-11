# db.py - Database Layer

## Overview
All DuckDB database operations for the KB Manager. Uses `C:\knowledge-base\kb.duckdb`.

## Connection
- `get_con()` - Opens a new DuckDB connection (caller must close)

## URL Registry

- `is_url_registered(url)` - Check if URL exists, returns dict with id/status/quality/refresh_requested
- `register_url(url, title, domain, quality_score, word_count, raw_file, status)` - Insert or update URL
- `assign_path(url_id, path)` - Assign category path to URL (no-op if already exists)
- `move_url_path(url_id, old_path, new_path)` - Move URL category and move files on disk

## Facts

- `migrate_facts_add_source()` - Migration: adds `source` column to facts table if missing
- `save_facts_to_db(url_id, facts, verified_entities, ddg_facts, reddit_facts, google_facts)` - Save facts from all sources with source labels: llm/ddg_facts/reddit/google/wikipedia
- `get_facts_for_explorer(verified, search, source)` - Query facts with optional filters

## Index Management

- `get_index_summary()` - Row counts for all tables (used by dashboard cards)
- `get_index_by_category()` - URL counts and avg quality per category
- `get_keywords_index(topic_filter)` - Keywords grouped by topic/source
- `get_facts_index(url_id)` - Facts with source URL info
- `get_questions_index(category_filter)` - Questions grouped by category/keyphrase
- `search_index(query)` - Unified search: urls, facts, keywords, questions
- `export_index_data(table)` - Full table export as list of dicts

## Delete / Cleanup

- `delete_fact(fact_id)` - Delete single fact
- `delete_facts_bulk(fact_ids)` - Bulk delete facts by ID list
- `delete_facts_by_url(url_id)` - Delete all facts for a URL
- `delete_keyword(keyword_id)` - Delete single keyword
- `delete_keywords_by_topic(topic)` - Delete all keywords for topic
- `delete_keywords_bulk(keyword_ids)` - Bulk delete keywords
- `delete_question(question_id)` - Delete single question
- `delete_questions_by_category(category)` - Delete all in category
- `delete_questions_bulk(question_ids)` - Bulk delete questions
- `delete_url(url_id)` - Delete URL + cascade facts + paths
- `delete_urls_below_quality(min_score)` - Bulk delete low quality URLs
- `cleanup_orphans()` - Remove facts/paths with no matching URL

## Database Schema

url_registry (id, url, title, domain, first_downloaded, last_downloaded, quality_score, word_count, raw_file, status, refresh_requested)

url_paths (url_id, path, assigned_at)

facts (id, url_id, fact, verified, source)

keyword_intelligence (id, topic, category, source, keyword, score, notes, analyzed_at)

questions_research (id, category, keyphrase, question, source, notes, analyzed_at)


