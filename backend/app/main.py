from fastapi import FastAPI


app = FastAPI(
    title="RepoLens API",
    version="0.1.0",
    description="GitHub repository understanding and issue triage service.",
)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}

