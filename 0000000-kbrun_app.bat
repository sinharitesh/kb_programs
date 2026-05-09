@echo off
echo Installing FastAPI dependencies...
cd /d C:\Users\sinha\OneDrive\Documents\github\kb_programs
pip install fastapi uvicorn jinja2 python-multipart --quiet
FOR /F "tokens=5" %%P IN ('netstat -ano ^| findstr :8000') DO taskkill /PID %%P /F
echo Starting Knowledge Base UI...
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
pause