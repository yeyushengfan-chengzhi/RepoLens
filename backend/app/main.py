import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
from typing import Awaitable, Callable
from uuid import uuid4

import certifi
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from .evidence import fetch_code_evidence
from .github import GitHubApiError, analyze_repository
from .hermes import HermesClient, PROMPT_VERSION, fallback_issue_analysis
from .schemas import (
    AnalysisJob,
    AnalyzeIssueRequest,
    AnalyzeRepositoryRequest,
    IssueAiAnalysis,
    RepositoryAnalysis,
)
from .storage import AnalysisCache


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_INDEX = PROJECT_ROOT / "frontend" / "index.html"
AI_CACHE_MAX_ITEMS = 256
AI_JOB_MAX_ITEMS = 512
ai_analysis_cache: OrderedDict[tuple[str, int, str, str], IssueAiAnalysis] = OrderedDict()
persistent_cache = AnalysisCache(
    os.getenv("REPOLENS_DB_PATH", str(PROJECT_ROOT / "data" / "repolens.db")),
    max_items=AI_CACHE_MAX_ITEMS,
)
_inflight_analyses: dict[
    tuple[str, int, str, str], asyncio.Task[IssueAiAnalysis]
] = {}
_inflight_lock = asyncio.Lock()
logger = logging.getLogger(__name__)


@dataclass
class _AnalysisJobRecord:
    id: str
    payload: AnalyzeIssueRequest
    state: str = "queued"
    stage: str = "queued"
    result: IssueAiAnalysis | None = None
    error: str | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def public(self) -> AnalysisJob:
        return AnalysisJob(
            id=self.id,
            state=self.state,
            stage=self.stage,
            result=self.result,
            error=self.error,
        )


analysis_jobs: OrderedDict[str, _AnalysisJobRecord] = OrderedDict()
_active_jobs: dict[tuple[str, int, str, str], str] = {}

app = FastAPI(
    title="RepoLens API",
    version="0.5.0",
    description="GitHub repository understanding and issue triage service.",
)


def _cache_key(payload: AnalyzeIssueRequest) -> tuple[str, int, str, str]:
    return (payload.repository, payload.number, payload.updated_at, PROMPT_VERSION)


async def _read_cached(payload: AnalyzeIssueRequest) -> IssueAiAnalysis | None:
    key = _cache_key(payload)
    cached = ai_analysis_cache.get(key)
    if cached is None:
        cached = await asyncio.to_thread(persistent_cache.get, *key)
        if cached is not None:
            ai_analysis_cache[key] = cached
    if cached is None:
        return None
    ai_analysis_cache.move_to_end(key)
    return cached.model_copy(update={"source": "cache"})


async def _store_analysis(
    payload: AnalyzeIssueRequest, analysis: IssueAiAnalysis
) -> None:
    key = _cache_key(payload)
    ai_analysis_cache[key] = analysis
    ai_analysis_cache.move_to_end(key)
    while len(ai_analysis_cache) > AI_CACHE_MAX_ITEMS:
        ai_analysis_cache.popitem(last=False)
    await asyncio.to_thread(persistent_cache.put, *key, analysis)


async def _generate_issue_analysis(
    payload: AnalyzeIssueRequest,
    progress: Callable[[str], Awaitable[None]] | None = None,
) -> IssueAiAnalysis:
    issue = {
        "number": payload.number,
        "title": payload.title,
        "body": payload.body,
        "labels": [{"name": label} for label in payload.labels],
    }
    async with httpx.AsyncClient(
        timeout=65.0,
        follow_redirects=True,
        verify=certifi.where(),
    ) as client:
        hermes = HermesClient(client)
        if not hermes.available:
            result = await hermes.analyze_issue(
                repository=payload.repository,
                issue=issue,
                candidate_paths=payload.candidate_paths,
                suggested_steps=payload.suggested_steps,
            )
        else:
            if progress is not None:
                await progress("fetching_evidence")
            evidence = await fetch_code_evidence(
                repository=payload.repository,
                issue=issue,
                candidate_paths=payload.candidate_paths,
                client=client,
            )
            if progress is not None:
                await progress("calling_hermes")
            result = await hermes.analyze_issue(
                repository=payload.repository,
                issue=issue,
                candidate_paths=payload.candidate_paths,
                suggested_steps=payload.suggested_steps,
                evidence=evidence,
            )
    if result.source == "hermes":
        await _store_analysis(payload, result)
    return result


async def _perform_issue_analysis(
    payload: AnalyzeIssueRequest,
    progress: Callable[[str], Awaitable[None]] | None = None,
) -> IssueAiAnalysis:
    cached = await _read_cached(payload)
    if cached is not None:
        if progress is not None:
            await progress("cache_hit")
        return cached

    key = _cache_key(payload)
    async with _inflight_lock:
        task = _inflight_analyses.get(key)
        if task is None:
            task = asyncio.create_task(_generate_issue_analysis(payload, progress))
            _inflight_analyses[key] = task
    try:
        return await asyncio.shield(task)
    finally:
        async with _inflight_lock:
            if _inflight_analyses.get(key) is task:
                _inflight_analyses.pop(key, None)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(FRONTEND_INDEX)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/api/v1/repositories/analyze",
    response_model=RepositoryAnalysis,
    tags=["repositories"],
)
async def analyze(payload: AnalyzeRepositoryRequest) -> RepositoryAnalysis:
    try:
        async with httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
            verify=certifi.where(),
        ) as client:
            return await analyze_repository(payload.repository_url, client)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GitHubApiError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="GitHub is temporarily unreachable. Please try again.",
        ) from exc


@app.post(
    "/api/v1/issues/analyze",
    response_model=IssueAiAnalysis,
    tags=["issues"],
)
async def analyze_issue(payload: AnalyzeIssueRequest) -> IssueAiAnalysis:
    return await _perform_issue_analysis(payload)


async def _update_job(job: _AnalysisJobRecord, stage: str) -> None:
    job.stage = stage
    job.changed.set()


async def _run_analysis_job(job: _AnalysisJobRecord) -> None:
    key = _cache_key(job.payload)
    job.state = "running"
    await _update_job(job, "checking_cache")
    try:
        job.result = await _perform_issue_analysis(
            job.payload,
            progress=lambda stage: _update_job(job, stage),
        )
        job.state = "completed"
        await _update_job(
            job,
            "completed" if job.result.source != "fallback" else "degraded",
        )
    except Exception as exc:  # The public job must terminate even on unexpected failures.
        logger.exception(
            "Issue analysis job failed: type=%s repository=%s issue=%s",
            type(exc).__name__,
            job.payload.repository.replace("\r", " ").replace("\n", " ")[:200],
            job.payload.number,
        )
        job.state = "failed"
        job.error = "Issue analysis failed unexpectedly. Please retry."
        job.result = fallback_issue_analysis(job.payload.suggested_steps)
        await _update_job(job, "failed")
    finally:
        if _active_jobs.get(key) == job.id:
            _active_jobs.pop(key, None)


@app.post(
    "/api/v1/issues/analyze/jobs",
    response_model=AnalysisJob,
    status_code=202,
    tags=["issues"],
)
async def create_analysis_job(payload: AnalyzeIssueRequest) -> AnalysisJob:
    key = _cache_key(payload)
    active_id = _active_jobs.get(key)
    if active_id is not None and active_id in analysis_jobs:
        return analysis_jobs[active_id].public()

    job = _AnalysisJobRecord(id=uuid4().hex, payload=payload)
    analysis_jobs[job.id] = job
    _active_jobs[key] = job.id
    while len(analysis_jobs) > AI_JOB_MAX_ITEMS:
        removable_id = next(
            (
                existing_id
                for existing_id, existing in analysis_jobs.items()
                if existing.state not in {"queued", "running"}
            ),
            None,
        )
        if removable_id is None:
            break
        analysis_jobs.pop(removable_id)
    asyncio.create_task(_run_analysis_job(job))
    return job.public()


@app.get(
    "/api/v1/issues/analyze/jobs/{job_id}",
    response_model=AnalysisJob,
    tags=["issues"],
)
async def get_analysis_job(job_id: str) -> AnalysisJob:
    job = analysis_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found.")
    return job.public()


@app.get(
    "/api/v1/issues/analyze/jobs/{job_id}/events",
    tags=["issues"],
)
async def stream_analysis_job(job_id: str) -> StreamingResponse:
    job = analysis_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found.")

    async def events():
        last_payload = ""
        while True:
            payload = job.public().model_dump(mode="json")
            serialized = json.dumps(payload, ensure_ascii=False)
            if serialized != last_payload:
                yield f"data: {serialized}\n\n"
                last_payload = serialized
            if job.state in {"completed", "failed"}:
                break
            job.changed.clear()
            try:
                await asyncio.wait_for(job.changed.wait(), timeout=15)
            except TimeoutError:
                yield ": keep-alive\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
