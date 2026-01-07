# app/routes/task_routes.py
"""
Task Routes - Background jobs, imports, and progress tracking

Routes:
- /tasks/{task_id}          - Celery task status
- /import                   - Import page
- /update_cases             - Start update job
- /update_cases/status      - Job status API
- /update_progress/{job_id} - Progress page
- /events/{job_id}          - SSE stream
"""

import os
import sys
import uuid
import asyncio
import tempfile
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.auth_service import get_current_user
from app.services.progress_bus import progress_bus
from app.services.update_cases_service import run_update_cases_job, LAST_UPDATE_STATUS

# Try to import config
try:
    from app.config import settings
except ImportError:
    class settings:
        is_celery_enabled = False

logger = logging.getLogger("pascowebapp.tasks")

router = APIRouter(tags=["tasks"])

# Templates - will be set by main.py
templates = None


def init_templates(t):
    """Initialize templates from main app"""
    global templates
    templates = t


# ============================================================
# CELERY TASK STATUS
# ============================================================

@router.get("/tasks/{task_id}", response_class=HTMLResponse)
def task_status(request: Request, task_id: str):
    """Check status of a Celery background task"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    
    if not settings.is_celery_enabled:
        return templates.TemplateResponse(
            "task_status.html",
            {"request": request, "error": "Background tasks not enabled", "current_user": user}
        )
    
    from app.celery_app import celery_app
    from celery.result import AsyncResult
    
    task = AsyncResult(task_id, app=celery_app)
    
    return templates.TemplateResponse(
        "task_status.html",
        {
            "request": request,
            "task_id": task_id,
            "status": task.state,
            "result": task.result if task.ready() else None,
            "current_user": user,
        }
    )


# ============================================================
# IMPORT PAGE
# ============================================================

@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request):
    """Import/Update cases page"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    
    return templates.TemplateResponse(
        "import.html",
        {"request": request, "current_user": user}
    )


# ============================================================
# UPDATE CASES JOB
# ============================================================

@router.get("/update_cases/status")
def update_cases_status(request: Request):
    """Get status of last update job"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    return LAST_UPDATE_STATUS


@router.post("/update_cases")
async def update_cases(
    request: Request,
    since_days: int = Form(7),
    run_pasco: Optional[int] = Form(None),
    run_pinellas: Optional[int] = Form(None),
):
    """
    Start async job to scrape and import foreclosure data
    
    Steps:
    1) Run foreclosure scraper with --since-days
    2) Import resulting CSV (upsert by case_number)
    
    Redirects to live progress page.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    
    job_id = uuid.uuid4().hex
    
    # Prime the log so progress page shows something immediately
    await progress_bus.publish(job_id, f"Queued job {job_id}…")
    
    # Start the job asynchronously
    asyncio.create_task(
        run_update_cases_job(
            job_id,
            since_days,
            run_pasco=bool(run_pasco),
            run_pinellas=bool(run_pinellas),
        )
    )
    
    return RedirectResponse(
        url=f"/update_progress/{job_id}",
        status_code=303,
    )


# ============================================================
# PROGRESS PAGE (Live Log)
# ============================================================

@router.get("/update_progress/{job_id}", response_class=HTMLResponse)
async def update_progress_page(request: Request, job_id: str):
    """Live progress page for update jobs"""
    html = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Updating cases…</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 0; }}
    .wrap {{ max-width: 900px; margin: 24px auto; padding: 0 16px; }}
    .spinner {{
      position: fixed; inset: 0; display: flex; align-items: center; justify-content: center;
      background: rgba(0,0,0,0.5); color: #fff; z-index: 9999; font-size: 18px;
    }}
    .log {{
      background: #0b0b0b; color: #c9f4ff; padding: 16px; border-radius: 12px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
      white-space: pre-wrap; line-height: 1.35; max-height: 60vh; overflow: auto;
      box-shadow: 0 10px 30px rgba(0,0,0,0.2);
    }}
    .muted {{ color: #9aa7b1; font-size: 12px; margin-top: 8px; }}
    .hide {{ display:none; }}
    .pill {{ display:inline-block; padding:4px 10px; border-radius: 999px; background:#eef2ff; color:#3730a3; font-size:12px; }}
  </style>
</head>
<body>
  <div id="spinner" class="spinner">Updating cases… Please don't navigate away.</div>
  <div class="wrap">
    <h1>Update in progress <span class="pill">live log</span></h1>
    <div id="log" class="log"></div>
    <div id="hint" class="muted">This log will auto-scroll. You'll be redirected when finished.</div>
  </div>

<script>
  const logEl = document.getElementById('log');
  const spinner = document.getElementById('spinner');
  const es = new EventSource('/events/{job_id}');
  function appendLine(s) {{
    logEl.textContent += s + '\\n';
    logEl.scrollTop = logEl.scrollHeight;
  }}
  es.onmessage = (e) => {{
    const t = e.data || '';
    if (t.startsWith('[done]')) {{
      spinner.classList.add('hide');
      es.close();
      setTimeout(() => window.location.href = '/cases', 10000);
    }} else {{
      if (t.trim().length) {{
        appendLine(t);
        spinner.classList.add('hide');
      }}
    }}
  }};
  es.onerror = () => {{
    appendLine('[connection error] retrying…');
  }};
</script>
</body>
</html>
"""
    return HTMLResponse(content=html)


# ============================================================
# SSE EVENTS STREAM
# ============================================================

@router.get("/events/{job_id}")
async def events_stream(job_id: str):
    """Server-Sent Events stream for job progress"""
    async def event_generator():
        # Initial hello to open stream
        yield ": connected\n\n"
        while True:
            try:
                async for line in progress_bus.stream(job_id):
                    yield f"data: {line}\n\n"
            except Exception:
                # Heartbeat to keep connection alive
                yield ": heartbeat\n\n"
                await asyncio.sleep(5)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
