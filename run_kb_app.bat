@echo off
cd C:\Users\sinha\git\kb_programs

echo Pulling latest changes...
git pull

echo Touching reload trigger...
echo # reload %date% %time% > dummy.py

echo Starting KB App...
python -m uvicorn app:app --reload --port 8000
