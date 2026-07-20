import asyncio
import base64
import re
from urllib.parse import quote

import httpx

from .github import GitHubApiError, GitHubClient, IGNORED_WORDS, REPOSITORY_PART, WORD_PART
from .schemas import CodeEvidence


MAX_EVIDENCE_FILES = 3
MAX_EVIDENCE_CHARS = 4500
MAX_SOURCE_BYTES = 160_000


def _keywords(issue: dict) -> set[str]:
    text = f"{issue.get('title') or ''} {str(issue.get('body') or '')[:3000]}".lower()
    return {
        word.lower().replace("-", "_")
        for word in WORD_PART.findall(text)
        if word.lower() not in IGNORED_WORDS and len(word) >= 4
    }


def _select_excerpt(source: str, keywords: set[str]) -> tuple[int, int, str] | None:
    if "\x00" in source:
        return None
    lines = source.splitlines()
    if not lines:
        return None

    scored: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        lowered = line.lower()
        score = sum(1 for keyword in keywords if keyword in lowered)
        if score:
            scored.append((score, index))
    if scored:
        _, center = max(scored, key=lambda item: (item[0], -item[1]))
        start = max(0, center - 6)
        end = min(len(lines), center + 13)
    else:
        start = next((index for index, line in enumerate(lines) if line.strip()), 0)
        end = min(len(lines), start + 18)

    numbered = [f"{index + 1}: {lines[index]}" for index in range(start, end)]
    excerpt = "\n".join(numbered).strip()
    if not excerpt:
        return None
    return start + 1, end, excerpt[:2500]


async def fetch_code_evidence(
    *,
    repository: str,
    issue: dict,
    candidate_paths: list[str],
    client: httpx.AsyncClient,
) -> list[CodeEvidence]:
    parts = repository.split("/", 1)
    if len(parts) != 2 or not all(REPOSITORY_PART.fullmatch(part) for part in parts):
        return []
    owner, name = parts
    safe_paths = [
        path
        for path in candidate_paths[:MAX_EVIDENCE_FILES]
        if path
        and len(path) <= 240
        and not path.startswith(("/", "\\"))
        and ".." not in path.replace("\\", "/").split("/")
    ]
    github = GitHubClient(client)

    async def fetch(path: str) -> tuple[str, str] | None:
        try:
            payload = await github.get_json(
                f"/repos/{owner}/{name}/contents/{quote(path, safe='/')}"
            )
        except (GitHubApiError, httpx.HTTPError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("encoding") != "base64":
            return None
        try:
            raw = base64.b64decode(str(payload.get("content") or ""), validate=False)
        except (ValueError, TypeError):
            return None
        if len(raw) > MAX_SOURCE_BYTES:
            return None
        return path, raw.decode("utf-8", errors="replace")

    fetched = await asyncio.gather(*(fetch(path) for path in safe_paths))
    keywords = _keywords(issue)
    evidence: list[CodeEvidence] = []
    remaining = MAX_EVIDENCE_CHARS
    for item in fetched:
        if item is None or remaining <= 0:
            continue
        path, source = item
        selected = _select_excerpt(source, keywords)
        if selected is None:
            continue
        start_line, end_line, excerpt = selected
        if len(excerpt) > remaining:
            excerpt_lines = excerpt.splitlines()
            kept_lines: list[str] = []
            used = 0
            for line in excerpt_lines:
                separator = 1 if kept_lines else 0
                available = remaining - used - separator
                if available <= 0:
                    break
                kept_lines.append(line[:available])
                used += len(kept_lines[-1]) + separator
                if len(line) > available:
                    break
            excerpt = "\n".join(kept_lines)
            end_line = start_line + len(kept_lines) - 1
        if not excerpt:
            continue
        evidence.append(
            CodeEvidence(
                id=f"E{len(evidence) + 1}",
                path=path,
                start_line=start_line,
                end_line=end_line,
                excerpt=excerpt,
            )
        )
        remaining -= len(excerpt)
    return evidence
