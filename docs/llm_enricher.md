# llm_enricher.py - LLM Enrichment

## Overview
Uses local Ollama LLM (qwen2.5:14b) to enrich scraped content with structured data.

## Key Functions

- `call_ollama(prompt)` - Send prompt to Ollama API, returns response text
- `extract_json(raw)` - Parse JSON from LLM response (handles markdown code blocks)
- `chunk_text(text)` - Split article by headings, fallback to fixed word chunks (2000 words)
- `enrich(scrape_result, user_category)` - Full enrichment pipeline: chunk → LLM per chunk → merge
- `verify_via_wikipedia(entity)` - Verify entity against Wikipedia API
- `write_wiki_file(enriched)` - Save enriched content as markdown to wiki folder
- `update_indexes(enriched)` - Update category index and master cross-reference files
- `process_scrape_result(scrape_result, user_category)` - Full pipeline: enrich → save wiki → register URL → save facts
- `fetch_ddg_facts(query)` - Fetch web snippets from DDG as verified facts

## Enrichment Output Schema
{ "title": "Article title", "summary": "4-5 sentence summary", "facts": ["fact 1", "fact 2"], "tags": ["tag1", "tag2"], "entities": ["entity1"], "cross_refs": ["path1"], "suggested_path": "category/sub/specific", "quality_score": 8, "verified_entities": { "Entity": {"verified": true, "wiki_summary": "...", "wiki_url": "..."} } }



## Text Cleaning
- `clean_text(text)` - Fixes camelCase concatenation from DDG snippets (e.g. "isHanumanTemple" → "is Hanuman Temple")

## LLM Configuration
- Model: `qwen2.5:14b`
- Temperature: 0.2
- Timeout: 180 seconds
- Ollama URL: `http://localhost:11434/api/generate`
