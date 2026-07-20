from pydantic import BaseModel, Field, HttpUrl


class AnalyzeRepositoryRequest(BaseModel):
    repository_url: str = Field(min_length=10, max_length=300)


class AnalyzeIssueRequest(BaseModel):
    repository: str = Field(min_length=3, max_length=200)
    number: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(default="", max_length=6000)
    updated_at: str = Field(default="", max_length=50)
    labels: list[str] = Field(default_factory=list, max_length=20)
    candidate_paths: list[str] = Field(default_factory=list, max_length=5)
    suggested_steps: list[str] = Field(min_length=1, max_length=12)


class RepositoryFile(BaseModel):
    name: str
    path: str
    kind: str


class CandidateFile(BaseModel):
    path: str
    score: int = Field(ge=1)
    reason: str


class CodeEvidence(BaseModel):
    id: str = Field(pattern=r"^E[1-9][0-9]*$")
    path: str = Field(min_length=1, max_length=240)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    excerpt: str = Field(min_length=1, max_length=2500)


class TokenUsage(BaseModel):
    prompt_tokens: int = Field(default=0, ge=0)
    prompt_cache_hit_tokens: int = Field(default=0, ge=0)
    prompt_cache_miss_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    estimated_cost_cny: float = Field(default=0, ge=0)
    pricing_model: str = "deepseek-v4-flash"


class IssueAiAnalysis(BaseModel):
    explanation: str = Field(min_length=1, max_length=4000)
    implementation_steps: list[str] = Field(min_length=1, max_length=12)
    test_plan: list[str] = Field(min_length=1, max_length=12)
    risks: list[str] = Field(min_length=1, max_length=12)
    source: str
    evidence: list[CodeEvidence] = Field(default_factory=list, max_length=5)
    evidence_ids: list[str] = Field(default_factory=list, max_length=5)
    usage: TokenUsage | None = None


class AnalysisJob(BaseModel):
    id: str
    state: str
    stage: str
    result: IssueAiAnalysis | None = None
    error: str | None = None


class IssueSummary(BaseModel):
    number: int
    title: str
    body: str
    url: HttpUrl
    labels: list[str]
    comments: int
    updated_at: str
    difficulty: str
    newcomer_score: int = Field(ge=0, le=100)
    recommendation: str
    candidate_files: list[CandidateFile]
    suggested_steps: list[str]
    ai_analysis: IssueAiAnalysis


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
