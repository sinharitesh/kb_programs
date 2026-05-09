@echo off
echo Setting up Knowledge Base...
cd /d C:\Users\sinha\OneDrive\Documents\github\kb_programs
pip install duckdb --quiet
python setup_kb.py
pause
