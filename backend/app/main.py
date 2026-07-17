from pathlib import Path

import certifi
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from .github import GitHubApiError, analyze_repository
from .schemas import AnalyzeRepositoryRequest, RepositoryAnalysis


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_INDEX = PROJECT_ROOT / "frontend" / "index.html"

app = FastAPI(
    title="RepoLens API",
    version="0.2.0",
    description="GitHub repository understanding and issue triage service.",
)


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
