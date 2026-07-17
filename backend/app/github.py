import asyncio
import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from .schemas import IssueSummary, RepositoryAnalysis, RepositoryFile


GITHUB_API_BASE = "https://api.github.com"
REPOSITORY_PART = re.compile(r"^[A-Za-z0-9_.-]+$")


class GitHubApiError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class RepositoryCoordinates:
    owner: str
    name: str


def parse_github_repository_url(repository_url: str) -> RepositoryCoordinates:
    raw = repository_url.strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Please enter a complete GitHub URL beginning with https://.")
    if (parsed.hostname or "").lower() not in {"github.com", "www.github.com"}:
        raise ValueError("Only public github.com repository URLs are supported.")

    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise ValueError("Use a repository URL such as https://github.com/owner/repository.")

    owner, name = parts
    if name.endswith(".git"):
        name = name[:-4]
    if not REPOSITORY_PART.fullmatch(owner) or not REPOSITORY_PART.fullmatch(name):
        raise ValueError("The GitHub owner or repository name is invalid.")
    return RepositoryCoordinates(owner=owner, name=name)


def assess_issue(issue: dict) -> tuple[str, int, str]:
    labels = {str(label.get("name", "")).strip().lower() for label in issue.get("labels", [])}
    comments = int(issue.get("comments", 0) or 0)
    body_length = len(issue.get("body") or "")

    score = 45
    reasons: list[str] = []

    if "good first issue" in labels:
        score += 40
        reasons.append("仓库标记为 good first issue")
    if "help wanted" in labels:
        score += 20
        reasons.append("维护者明确希望社区参与")
    if labels.intersection({"documentation", "docs"}):
        score += 15
        reasons.append("文档类改动通常更容易验证")
    if labels.intersection({"blocked", "security", "breaking change"}):
        score -= 30
        reasons.append("包含高风险或阻塞标签")
    if comments > 10:
        score -= 15
        reasons.append("讨论较长，可能存在隐藏上下文")
    if body_length > 5000:
        score -= 10
        reasons.append("问题描述较长，范围可能较大")

    score = max(0, min(100, score))
    if score >= 70:
        difficulty = "入门"
    elif score >= 40:
        difficulty = "中等"
    else:
        difficulty = "较难"

    recommendation = "；".join(reasons) if reasons else "需要进一步人工确认修改范围"
    return difficulty, score, recommendation


class GitHubClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.rate_limit_remaining: int | None = None

    async def get_json(self, path: str, params: dict | None = None):
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "RepoLens/0.2",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = os.getenv("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        response = await self.client.get(
            f"{GITHUB_API_BASE}{path}", headers=headers, params=params
        )
        remaining = response.headers.get("x-ratelimit-remaining")
        if remaining and remaining.isdigit():
            self.rate_limit_remaining = int(remaining)

        if response.status_code == 404:
            raise GitHubApiError("Repository not found or not publicly accessible.", 404)
        if response.status_code == 403:
            raise GitHubApiError(
                "GitHub API rate limit reached. Try later or configure GITHUB_TOKEN.",
                429,
            )
        if response.is_error:
            raise GitHubApiError(
                f"GitHub API returned HTTP {response.status_code}.", 502
            )
        return response.json()


async def analyze_repository(
    repository_url: str, client: httpx.AsyncClient
) -> RepositoryAnalysis:
    coordinates = parse_github_repository_url(repository_url)
    github = GitHubClient(client)
    base = f"/repos/{coordinates.owner}/{coordinates.name}"

    repository, languages, contents, issues = await asyncio.gather(
        github.get_json(base),
        github.get_json(f"{base}/languages"),
        github.get_json(f"{base}/contents"),
        github.get_json(
            f"{base}/issues",
            params={"state": "open", "sort": "updated", "per_page": 30},
        ),
    )

    root_files = [
        RepositoryFile(
            name=item.get("name", ""),
            path=item.get("path", ""),
            kind=item.get("type", "unknown"),
        )
        for item in contents[:60]
        if item.get("name")
    ]

    issue_summaries: list[IssueSummary] = []
    for issue in issues:
        if "pull_request" in issue:
            continue
        difficulty, score, recommendation = assess_issue(issue)
        issue_summaries.append(
            IssueSummary(
                number=issue["number"],
                title=issue["title"],
                url=issue["html_url"],
                labels=[label.get("name", "") for label in issue.get("labels", [])],
                comments=issue.get("comments", 0),
                updated_at=issue.get("updated_at", ""),
                difficulty=difficulty,
                newcomer_score=score,
                recommendation=recommendation,
            )
        )

    issue_summaries.sort(key=lambda item: item.newcomer_score, reverse=True)
    license_info = repository.get("license") or {}

    return RepositoryAnalysis(
        owner=coordinates.owner,
        name=coordinates.name,
        full_name=repository["full_name"],
        url=repository["html_url"],
        description=repository.get("description"),
        default_branch=repository.get("default_branch", "main"),
        stars=repository.get("stargazers_count", 0),
        forks=repository.get("forks_count", 0),
        open_issue_count=repository.get("open_issues_count", 0),
        license=license_info.get("spdx_id"),
        languages=languages,
        root_files=root_files,
        issues=issue_summaries[:15],
        github_rate_limit_remaining=github.rate_limit_remaining,
    )
