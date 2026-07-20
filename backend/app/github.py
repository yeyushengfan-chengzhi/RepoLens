import asyncio
import os
import re
from dataclasses import dataclass
from urllib.parse import quote, urlparse

import httpx

from .hermes import pending_issue_analysis
from .schemas import CandidateFile, IssueSummary, RepositoryAnalysis, RepositoryFile


GITHUB_API_BASE = "https://api.github.com"
REPOSITORY_PART = re.compile(r"^[A-Za-z0-9_.-]+$")
WORD_PART = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{2,}")
IGNORED_WORDS = {
    "about",
    "add",
    "after",
    "also",
    "and",
    "are",
    "before",
    "can",
    "could",
    "data",
    "error",
    "from",
    "have",
    "issue",
    "into",
    "open",
    "please",
    "read",
    "repository",
    "should",
    "that",
    "the",
    "this",
    "using",
    "user",
    "users",
    "when",
    "where",
    "with",
}
IGNORED_PATH_PARTS = {
    ".git",
    ".github",
    "build",
    "dist",
    "node_modules",
    "vendor",
}


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


def recommend_issue_files(issue: dict, tree: list[dict]) -> list[CandidateFile]:
    """Rank repository files by transparent keyword and path heuristics."""
    title = str(issue.get("title") or "")
    body = str(issue.get("body") or "")
    title_text = title.lower()
    body_text = body[:4000].lower()
    searchable_text = f"{title_text} {body_text}"
    title_keywords = {
        word.lower().replace("-", "_")
        for word in WORD_PART.findall(title_text)
        if word.lower() not in IGNORED_WORDS
    }
    body_keywords = {
        word.lower().replace("-", "_")
        for word in WORD_PART.findall(body_text)
        if word.lower() not in IGNORED_WORDS and len(word) >= 4
    } - title_keywords
    labels = {
        str(label.get("name", "")).strip().lower()
        for label in issue.get("labels", [])
    }
    wants_docs = bool(labels.intersection({"documentation", "docs"})) or any(
        word in title_text for word in ("document", "readme", "typo")
    )
    wants_tests = any(word in searchable_text for word in ("test", "regression", "bug"))

    ranked: list[CandidateFile] = []
    for item in tree:
        if item.get("type") != "blob":
            continue
        path = str(item.get("path") or "")
        if not path or len(path) > 240:
            continue
        lower_path = path.lower()
        parts = set(re.split(r"[/._-]+", lower_path))
        if parts.intersection(IGNORED_PATH_PARTS):
            continue
        if lower_path.endswith((".lock", ".min.js", ".map", ".svg", ".png", ".jpg")):
            continue

        title_matches = sorted(
            keyword
            for keyword in title_keywords
            if keyword in parts or (len(keyword) >= 5 and keyword in lower_path)
        )
        body_matches = sorted(
            keyword
            for keyword in body_keywords
            if keyword in parts or (len(keyword) >= 6 and keyword in lower_path)
        )
        score = sum(8 if keyword in parts else 4 for keyword in title_matches)
        score += sum(4 if keyword in parts else 1 for keyword in body_matches)
        reasons: list[str] = []
        if title_matches:
            reasons.append("路径命中标题关键词：" + "、".join(title_matches[:3]))
        if body_matches:
            reasons.append("路径命中正文关键词：" + "、".join(body_matches[:3]))
        if wants_docs and (lower_path.startswith("docs/") or "readme" in lower_path):
            score += 8
            reasons.append("Issue 涉及文档")
        if wants_tests and any(part in parts for part in ("test", "tests", "spec")):
            score += 5
            reasons.append("Issue 可能需要测试验证")

        if score >= 4:
            ranked.append(
                CandidateFile(
                    path=path,
                    score=score,
                    reason="；".join(reasons),
                )
            )

    ranked.sort(key=lambda item: (-item.score, len(item.path), item.path))
    return ranked[:5]


def suggest_issue_steps(issue: dict, candidates: list[CandidateFile]) -> list[str]:
    steps = ["先阅读 Issue 原文和讨论，确认复现条件与验收标准。"]
    if candidates:
        steps.append(f"从 {candidates[0].path} 开始定位相关逻辑。")
    else:
        steps.append("先用 Issue 标题中的关键词在仓库内搜索相关代码。")
    steps.append("建立独立 feature/fix 分支，用最小改动完成修复或功能。")
    steps.append("运行现有测试，并为本次改动补充一个可复现的测试。")
    return steps


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

    repository = await github.get_json(base)
    default_branch = repository.get("default_branch", "main")
    languages, contents, issues, tree_payload = await asyncio.gather(
        github.get_json(f"{base}/languages"),
        github.get_json(f"{base}/contents"),
        github.get_json(
            f"{base}/issues",
            params={"state": "open", "sort": "updated", "per_page": 30},
        ),
        github.get_json(
            f"{base}/git/trees/{quote(default_branch, safe='')}",
            params={"recursive": "1"},
        ),
    )
    repository_tree = tree_payload.get("tree", [])

    root_files = [
        RepositoryFile(
            name=item.get("name", ""),
            path=item.get("path", ""),
            kind=item.get("type", "unknown"),
        )
        for item in contents[:60]
        if item.get("name")
    ]

    ranked_issues: list[tuple[dict, str, int, str, list[CandidateFile], list[str]]] = []
    for issue in issues:
        if "pull_request" in issue:
            continue
        difficulty, score, recommendation = assess_issue(issue)
        candidate_files = recommend_issue_files(issue, repository_tree)
        suggested_steps = suggest_issue_steps(issue, candidate_files)
        ranked_issues.append(
            (issue, difficulty, score, recommendation, candidate_files, suggested_steps)
        )

    ranked_issues.sort(key=lambda item: item[2], reverse=True)
    selected_issues = ranked_issues[:15]
    issue_summaries: list[IssueSummary] = []
    for item in selected_issues:
        issue, difficulty, score, recommendation, candidate_files, suggested_steps = item
        issue_summaries.append(
            IssueSummary(
                number=issue["number"],
                title=issue["title"],
                body=str(issue.get("body") or "")[:6000],
                url=issue["html_url"],
                labels=[label.get("name", "") for label in issue.get("labels", [])],
                comments=issue.get("comments", 0),
                updated_at=issue.get("updated_at", ""),
                difficulty=difficulty,
                newcomer_score=score,
                recommendation=recommendation,
                candidate_files=candidate_files,
                suggested_steps=suggested_steps,
                ai_analysis=pending_issue_analysis(suggested_steps),
            )
        )
    license_info = repository.get("license") or {}

    return RepositoryAnalysis(
        owner=coordinates.owner,
        name=coordinates.name,
        full_name=repository["full_name"],
        url=repository["html_url"],
        description=repository.get("description"),
        default_branch=default_branch,
        stars=repository.get("stargazers_count", 0),
        forks=repository.get("forks_count", 0),
        open_issue_count=repository.get("open_issues_count", 0),
        license=license_info.get("spdx_id"),
        languages=languages,
        root_files=root_files,
        issues=issue_summaries,
        github_rate_limit_remaining=github.rate_limit_remaining,
    )
