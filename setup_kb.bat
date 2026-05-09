@echo off
echo Installing scraper dependencies...
cd /d C:\Users\sinha\OneDrive\Documents\github\kb_programs
pip install httpx beautifulsoup4 lxml duckdb --quiet
echo Done. Scraper module ready.
pause
