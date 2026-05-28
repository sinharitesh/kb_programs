@echo off
cd C:\Users\sinha\git\kb_programs

echo Pulling latest changes...
git pull

echo Starting KB App...
python -m uvicorn app:app --reload --port 8000
