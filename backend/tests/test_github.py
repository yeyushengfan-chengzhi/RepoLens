import unittest

from fastapi.testclient import TestClient

from backend.app.github import assess_issue, parse_github_repository_url
from backend.app.main import app


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


class ApplicationTests(unittest.TestCase):
    def test_health_endpoint(self) -> None:
        response = TestClient(app).get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
