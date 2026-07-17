from pydantic import BaseModel, Field, HttpUrl


class AnalyzeRepositoryRequest(BaseModel):
    repository_url: str = Field(min_length=10, max_length=300)


class RepositoryFile(BaseModel):
    name: str
    path: str
    kind: str


class IssueSummary(BaseModel):
    number: int
    title: str
    url: HttpUrl
    labels: list[str]
    comments: int
    updated_at: str
    difficulty: str
    newcomer_score: int = Field(ge=0, le=100)
    recommendation: str


class RepositoryAnalysis(BaseModel):
    owner: str
    name: str
    full_name: str
    url: HttpUrl
    description: str | None
    default_branch: str
    stars: int
    forks: int
    open_issue_count: int
    license: str | None
    languages: dict[str, int]
    root_files: list[RepositoryFile]
    issues: list[IssueSummary]
    github_rate_limit_remaining: int | None

