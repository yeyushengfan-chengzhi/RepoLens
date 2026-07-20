import base64
import tempfile
import unittest

import httpx

from backend.app.evidence import fetch_code_evidence
from backend.app.schemas import IssueAiAnalysis, TokenUsage
from backend.app.storage import AnalysisCache


class AnalysisCacheTests(unittest.TestCase):
    def test_persists_analysis_across_cache_instances(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/analysis.db"
            analysis = IssueAiAnalysis(
                explanation="Persistent result",
                implementation_steps=["Inspect"],
                test_plan=["Test"],
                risks=["Risk"],
                source="hermes",
                usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            )
            first = AnalysisCache(path)
            first.put("owner/repo", 1, "updated", "prompt-v1", analysis)
            first.close()

            second = AnalysisCache(path)
            restored = second.get("owner/repo", 1, "updated", "prompt-v1")
            second.close()

        self.assertIsNotNone(restored)
        self.assertEqual(restored.explanation, "Persistent result")
        self.assertEqual(restored.usage.total_tokens, 15)

    def test_evicts_oldest_persistent_entry(self) -> None:
        cache = AnalysisCache(":memory:", max_items=2)
        analysis = IssueAiAnalysis(
            explanation="Result",
            implementation_steps=["Inspect"],
            test_plan=["Test"],
            risks=["Risk"],
            source="hermes",
        )
        cache.put("owner/repo", 1, "a", "v1", analysis)
        cache.put("owner/repo", 2, "b", "v1", analysis)
        cache.put("owner/repo", 3, "c", "v1", analysis)

        self.assertEqual(cache.count(), 2)
        self.assertIsNone(cache.get("owner/repo", 1, "a", "v1"))
        self.assertIsNotNone(cache.get("owner/repo", 3, "c", "v1"))
        cache.close()


class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_numbered_code_evidence(self) -> None:
        source = "\n".join(
            [
                "from collections import OrderedDict",
                "",
                "def analyze_issue(payload):",
                "    cache_key = payload.number",
                "    return cache_key",
            ]
        )

        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertIn("/repos/owner/repo/contents/backend/app/main.py", str(request.url))
            return httpx.Response(
                200,
                json={
                    "encoding": "base64",
                    "content": base64.b64encode(source.encode()).decode(),
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            evidence = await fetch_code_evidence(
                repository="owner/repo",
                issue={"title": "Fix cache bug", "body": "cache duplicates work"},
                candidate_paths=["backend/app/main.py"],
                client=client,
            )

        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].id, "E1")
        self.assertIn("4:     cache_key", evidence[0].excerpt)
        self.assertLessEqual(evidence[0].start_line, 4)
        self.assertGreaterEqual(evidence[0].end_line, 4)

    async def test_rejects_path_traversal_without_request(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.fail("Unsafe candidate path must not reach GitHub")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            evidence = await fetch_code_evidence(
                repository="owner/repo",
                issue={"title": "Read secret"},
                candidate_paths=["../../secret"],
                client=client,
            )

        self.assertEqual(evidence, [])


if __name__ == "__main__":
    unittest.main()
