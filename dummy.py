# dummy reload trigger — use touch() to restart uvicorn

from datetime import datetime

def touch():
    "Write a timestamp to trigger uvicorn reload"
    with open(__file__, 'w') as f:
        f.write(f'# reload {datetime.now().isoformat()}\n')

# reload 2026-05-28T16:12:00
