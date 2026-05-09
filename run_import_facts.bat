@echo off
cd C:\Users\sinha\git\kb_programs
echo Pulling latest...
git pull
echo.
echo Running one-time facts import...
python import_facts_once.py
