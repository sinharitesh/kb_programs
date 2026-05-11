# keyword_intelligence.py - Keyword Intelligence

## Overview
Multi-source keyword research: Google Suggest, Wikipedia, Reddit, DuckDuckGo.

## Key Functions

- `fetch_duckduckgo_facts(query, max_results)` - DDG text search with camelCase text cleaning
- `fetch_wikipedia_via_ddg(query)` - Wikipedia article titles via DDG
- `google_suggestions(query)` - Google autocomplete suggestions via Firefox endpoint
- `wikipedia_related(query)` - Wikipedia search results via API (with User-Agent header)
- `reddit_trending(query)` - Reddit search posts with upvote scores
- `run_keyword_intelligence(topic, category)` - Run all sources, save to DuckDB, return combined data
- `save_to_duckdb(topic, category, data)` - Persist keyword data to `keyword_intelligence` table
- `save_keyword_report(topic, category_path, data)` - Save markdown report to wiki folder
- `fetch_questions(topic)` - Google suggest questions (who/why/how/what/when/where + topic)
- `save_questions(category, keyphrase, questions)` - Save questions to `questions_research` table

## Text Cleaning
- `clean_text(text)` - Fixes camelCase concatenation from DDG (e.g. "inConnaughtPlace" → "in Connaught Place")

## Data Sources
| Source | Method | Data |
|--------|--------|------|
| Google | Suggest API (Firefox endpoint) | Autocomplete suggestions |
| Wikipedia | REST API | Related article titles |
| Reddit | JSON search API | Post titles + upvote scores |
| DuckDuckGo | DDGS library | Web snippets + URLs |
