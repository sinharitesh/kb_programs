"""
Persistent synthesis queue manager.
Stores synthesis jobs in JSON file for persistence across restarts.
"""
import json
import os
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

KB_ROOT = Path(r"C:\knowledge-base")
QUEUE_FILE = KB_ROOT / "synthesis_queue.json"

class SynthesisQueue:
    def __init__(self):
        self.jobs: Dict[str, dict] = {}
        self._load()
    
    def _load(self):
        """Load jobs from JSON file."""
        if QUEUE_FILE.exists():
            try:
                with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
                    self.jobs = json.load(f)
            except Exception as e:
                print(f"[SynthesisQueue] Error loading: {e}")
                self.jobs = {}
        else:
            self.jobs = {}
    
    def _save(self):
        """Save jobs to JSON file."""
        try:
            KB_ROOT.mkdir(parents=True, exist_ok=True)
            with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.jobs, f, indent=2, default=str)
        except Exception as e:
            print(f"[SynthesisQueue] Error saving: {e}")
    
    def create_job(self, keywords: List[str]) -> str:
        """Create a new synthesis job."""
        import hashlib
        job_id = hashlib.md5(f"{'_'.join(keywords)}{datetime.now()}".encode()).hexdigest()[:8]
        
        self.jobs[job_id] = {
            "id": job_id,
            "keywords": keywords,
            "status": "queued",
            "created_at": datetime.now().isoformat(),
            "completed_at": None,
            "results": [],
            "urls_found": [],
            "urls_skipped": [],
            "urls_processed": []
        }
        self._save()
        return job_id
    
    def get_job(self, job_id: str) -> Optional[dict]:
        """Get a job by ID."""
        return self.jobs.get(job_id)
    
    def update_job(self, job_id: str, **updates):
        """Update job fields."""
        if job_id in self.jobs:
            self.jobs[job_id].update(updates)
            self._save()
    
    def list_jobs(self, limit: int = 50) -> List[dict]:
        """List all jobs, most recent first."""
        jobs = sorted(
            self.jobs.values(),
            key=lambda x: x.get("created_at", ""),
            reverse=True
        )
        return jobs[:limit]
    
    def delete_job(self, job_id: str) -> bool:
        """Delete a job."""
        if job_id in self.jobs:
            del self.jobs[job_id]
            self._save()
            return True
        return False
    
    def clear_completed(self, older_than_hours: int = 24):
        """Clear completed jobs older than specified hours."""
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(hours=older_than_hours)
        
        to_delete = []
        for job_id, job in self.jobs.items():
            if job.get("status") == "done":
                completed_at = job.get("completed_at")
                if completed_at:
                    try:
                        completed = datetime.fromisoformat(completed_at)
                        if completed < cutoff:
                            to_delete.append(job_id)
                    except:
                        pass
        
        for job_id in to_delete:
            del self.jobs[job_id]
        
        if to_delete:
            self._save()
        
        return len(to_delete)

# Global queue instance
synth_queue = SynthesisQueue()