import asyncio
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.app.github import (
    assess_issue,
    parse_github_repository_url,
    recommend_issue_files,
    suggest_issue_steps,
)
from backend.app.hermes import PROMPT_VERSION
from backend.app.main import (
    AI_CACHE_MAX_ITEMS,
    _active_jobs,
    ai_analysis_cache,
    analysis_jobs,
    app,
)
from backend.app.schemas import IssueAiAnalysis
from backend.app.storage import AnalysisCache


class GitHubParsingTests(unittest.TestCase):
    def test_parses_repository_url(self) -> None:
        result = parse_github_repository_url(
            "https://github.com/open-webui/open-webui.git"
        )
        self.assertEqual(result.owner, "open-webui")
        self.assertEqual(result.name, "open-webui")

    def test_rejects_non_github_host(self) -> None:
        with self.assertRaises(ValueError):
            parse_github_repository_url("https://example.com/owner/repository")

    def test_rejects_non_repository_path(self) -> None:
        with self.assertRaises(ValueError):
            parse_github_repository_url("https://github.com/owner/repository/issues")

    def test_good_first_issue_scores_highly(self) -> None:
        difficulty, score, recommendation = assess_issue(
            {
                "labels": [{"name": "good first issue"}, {"name": "help wanted"}],
                "comments": 1,
                "body": "Small focused change",
            }
        )
        self.assertEqual(difficulty, "入门")
        self.assertGreaterEqual(score, 70)
        self.assertIn("good first issue", recommendation)

    def test_recommends_files_matching_issue_keywords(self) -> None:
        issue = {
            "title": "Fix authentication login regression",
            "body": "The auth service rejects a valid user.",
            "labels": [{"name": "bug"}],
        }
        tree = [
            {"type": "blob", "path": "backend/app/auth/service.py"},
            {"type": "blob", "path": "backend/tests/test_auth.py"},
            {"type": "blob", "path": "frontend/theme.css"},
        ]

        candidates = recommend_issue_files(issue, tree)

        self.assertEqual(candidates[0].path, "backend/tests/test_auth.py")
        self.assertNotIn("frontend/theme.css", [item.path for item in candidates])

    def test_suggested_steps_name_top_candidate(self) -> None:
        candidates = recommend_issue_files(
            {"title": "Update README", "body": "", "labels": []},
            [{"type": "blob", "path": "README.md"}],
        )
        steps = suggest_issue_steps({}, candidates)
        self.assertIn("README.md", steps[1])


class ApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.test_cache = AnalysisCache(
            f"{self.temporary_directory.name}/analysis.db",
            max_items=AI_CACHE_MAX_ITEMS,
        )
        self.addCleanup(self.test_cache.close)
        self.cache_patcher = patch(
            "backend.app.main.persistent_cache", self.test_cache
        )
        self.cache_patcher.start()
        self.addCleanup(self.cache_patcher.stop)
        ai_analysis_cache.clear()
        analysis_jobs.clear()
        _active_jobs.clear()

    def test_health_endpoint(self) -> None:
        response = TestClient(app).get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_ai_cache_has_a_bounded_capacity(self) -> None:
        self.assertEqual(AI_CACHE_MAX_ITEMS, 256)
        ai_analysis_cache.clear()

    def test_issue_analysis_reuses_cached_hermes_result(self) -> None:
        ai_analysis_cache.clear()
        generated = IssueAiAnalysis(
            explanation="解释",
            implementation_steps=["实施"],
            test_plan=["测试"],
            risks=["风险"],
            source="hermes",
        )
        payload = {
            "repository": "owner/repository",
            "number": 12,
            "title": "Fix issue",
            "body": "Details",
            "updated_at": "2026-07-17T00:00:00Z",
            "labels": ["bug"],
            "candidate_paths": ["backend/app.py"],
            "suggested_steps": ["先复现"],
        }
        mocked = AsyncMock(return_value=generated)
        with patch("backend.app.main.HermesClient.analyze_issue", mocked):
            client = TestClient(app)
            first = client.post("/api/v1/issues/analyze", json=payload)
            second = client.post("/api/v1/issues/analyze", json=payload)

        self.assertEqual(first.json()["source"], "hermes")
        self.assertEqual(second.json()["source"], "cache")
        self.assertEqual(mocked.await_count, 1)

    def test_fallback_issue_analysis_is_not_cached(self) -> None:
        ai_analysis_cache.clear()
        fallback = IssueAiAnalysis(
            explanation="Hermes unavailable",
            implementation_steps=["Inspect issue"],
            test_plan=["Run tests"],
            risks=["Confirm scope"],
            source="fallback",
        )
        payload = {
            "repository": "owner/repository",
            "number": 13,
            "title": "Fix issue",
            "updated_at": "2026-07-20T00:00:00Z",
            "suggested_steps": ["Inspect issue"],
        }
        mocked = AsyncMock(return_value=fallback)
        with patch("backend.app.main.HermesClient.analyze_issue", mocked):
            client = TestClient(app)
            client.post("/api/v1/issues/analyze", json=payload)
            client.post("/api/v1/issues/analyze", json=payload)

        self.assertEqual(mocked.await_count, 2)
        self.assertEqual(len(ai_analysis_cache), 0)

    def test_ai_cache_evicts_least_recently_used_entry(self) -> None:
        ai_analysis_cache.clear()
        cached = IssueAiAnalysis(
            explanation="Cached",
            implementation_steps=["Implement"],
            test_plan=["Test"],
            risks=["Risk"],
            source="hermes",
        )
        for number in range(AI_CACHE_MAX_ITEMS):
            ai_analysis_cache[
                ("owner/repository", number + 1, "old", PROMPT_VERSION)
            ] = cached

        generated = cached.model_copy(update={"explanation": "Newest"})
        payload = {
            "repository": "owner/repository",
            "number": 999,
            "title": "New issue",
            "updated_at": "new",
            "suggested_steps": ["Implement"],
        }
        with patch(
            "backend.app.main.HermesClient.analyze_issue",
            AsyncMock(return_value=generated),
        ):
            response = TestClient(app).post("/api/v1/issues/analyze", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(ai_analysis_cache), AI_CACHE_MAX_ITEMS)
        self.assertNotIn(
            ("owner/repository", 1, "old", PROMPT_VERSION), ai_analysis_cache
        )
        self.assertIn(
            ("owner/repository", 999, "new", PROMPT_VERSION), ai_analysis_cache
        )
        ai_analysis_cache.clear()

    def test_repository_to_issue_analysis_and_cache_workflow(self) -> None:
        ai_analysis_cache.clear()
        repository_result = {
            "owner": "owner",
            "name": "repository",
            "full_name": "owner/repository",
            "url": "https://github.com/owner/repository",
            "description": "Demo repository",
            "default_branch": "main",
            "stars": 10,
            "forks": 2,
            "open_issue_count": 1,
            "license": "MIT",
            "languages": {"Python": 100},
            "root_files": [{"name": "backend", "path": "backend", "kind": "dir"}],
            "issues": [
                {
                    "number": 21,
                    "title": "Fix cache race",
                    "body": "Concurrent requests duplicate work.",
                    "url": "https://github.com/owner/repository/issues/21",
                    "labels": ["bug"],
                    "comments": 1,
                    "updated_at": "2026-07-20T00:00:00Z",
                    "difficulty": "中等",
                    "newcomer_score": 55,
                    "recommendation": "需要确认并发范围",
                    "candidate_files": [
                        {"path": "backend/app/main.py", "score": 8, "reason": "路径命中 cache"}
                    ],
                    "suggested_steps": ["确认复现条件", "检查候选文件", "完成修改", "补充测试"],
                    "ai_analysis": {
                        "explanation": "AI 分析正在后台排队。",
                        "implementation_steps": ["确认复现条件"],
                        "test_plan": ["等待 AI 分析"],
                        "risks": ["等待 AI 分析"],
                        "source": "pending",
                    },
                }
            ],
            "github_rate_limit_remaining": 59,
        }
        generated = IssueAiAnalysis(
            explanation="Evidence-based analysis",
            implementation_steps=["Confirm", "Inspect", "Change", "Review"],
            test_plan=["Regression", "Module", "Manual"],
            risks=["Scope", "Dependency"],
            source="hermes",
        )

        with patch(
            "backend.app.main.analyze_repository",
            AsyncMock(return_value=repository_result),
        ), patch(
            "backend.app.main.HermesClient.analyze_issue",
            AsyncMock(return_value=generated),
        ) as mocked_hermes:
            client = TestClient(app)
            repository_response = client.post(
                "/api/v1/repositories/analyze",
                json={"repository_url": "https://github.com/owner/repository"},
            )
            issue = repository_response.json()["issues"][0]
            issue_payload = {
                "repository": repository_result["full_name"],
                "number": issue["number"],
                "title": issue["title"],
                "body": issue["body"],
                "updated_at": issue["updated_at"],
                "labels": issue["labels"],
                "candidate_paths": [item["path"] for item in issue["candidate_files"]],
                "suggested_steps": issue["suggested_steps"],
            }
            generated_response = client.post("/api/v1/issues/analyze", json=issue_payload)
            cached_response = client.post("/api/v1/issues/analyze", json=issue_payload)

        self.assertEqual(repository_response.status_code, 200)
        self.assertEqual(issue["ai_analysis"]["source"], "pending")
        self.assertEqual(generated_response.json()["source"], "hermes")
        self.assertEqual(cached_response.json()["source"], "cache")
        self.assertEqual(mocked_hermes.await_count, 1)
        ai_analysis_cache.clear()

    def test_async_job_reports_progress_and_sse_result(self) -> None:
        generated = IssueAiAnalysis(
            explanation="Async result",
            implementation_steps=["Confirm", "Inspect", "Change", "Review"],
            test_plan=["Regression", "Module", "Manual"],
            risks=["Scope", "Dependency"],
            source="hermes",
        )

        async def perform(payload, progress=None):
            if progress is not None:
                await progress("fetching_evidence")
            await asyncio.sleep(0.02)
            if progress is not None:
                await progress("calling_hermes")
            return generated

        payload = {
            "repository": "owner/repository",
            "number": 31,
            "title": "Async issue",
            "updated_at": "2026-07-20T01:00:00Z",
            "suggested_steps": ["Inspect"],
        }
        with patch("backend.app.main._perform_issue_analysis", side_effect=perform):
            with TestClient(app) as client:
                created = client.post("/api/v1/issues/analyze/jobs", json=payload)
                self.assertEqual(created.status_code, 202)
                job_id = created.json()["id"]
                job = created.json()
                for _ in range(50):
                    job = client.get(
                        f"/api/v1/issues/analyze/jobs/{job_id}"
                    ).json()
                    if job["state"] == "completed":
                        break
                    time.sleep(0.01)
                with client.stream(
                    "GET", f"/api/v1/issues/analyze/jobs/{job_id}/events"
                ) as response:
                    event_lines = [line for line in response.iter_lines() if line]

        self.assertEqual(job["state"], "completed")
        self.assertEqual(job["stage"], "completed")
        self.assertEqual(job["result"]["source"], "hermes")
        self.assertTrue(any('"state": "completed"' in line for line in event_lines))

    def test_duplicate_async_requests_reuse_active_job(self) -> None:
        generated = IssueAiAnalysis(
            explanation="Async result",
            implementation_steps=["Confirm"],
            test_plan=["Test"],
            risks=["Risk"],
            source="hermes",
        )

        async def perform(payload, progress=None):
            await asyncio.sleep(0.1)
            return generated

        payload = {
            "repository": "owner/repository",
            "number": 32,
            "title": "Duplicate async issue",
            "updated_at": "2026-07-20T02:00:00Z",
            "suggested_steps": ["Inspect"],
        }
        with patch("backend.app.main._perform_issue_analysis", side_effect=perform):
            with TestClient(app) as client:
                first = client.post("/api/v1/issues/analyze/jobs", json=payload)
                second = client.post("/api/v1/issues/analyze/jobs", json=payload)

        self.assertEqual(first.json()["id"], second.json()["id"])


class FrontendPolicyTests(unittest.TestCase):
    def test_only_top_issue_is_automatically_analyzed_by_default(self) -> None:
        html = (
            Path(__file__).resolve().parents[2] / "frontend" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn('<option value="top-1" selected>', html)
        self.assertIn("payload.issues.slice(0, 1)", html)
        self.assertNotIn("payload.issues.slice(0, 3)", html)


if __name__ == "__main__":
    unittest.main()
