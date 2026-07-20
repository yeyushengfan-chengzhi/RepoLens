import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.github import recommend_issue_files


DEFAULT_CASES = PROJECT_ROOT / "evaluation" / "cases.json"


def evaluate(cases: list[dict]) -> dict:
    results = []
    top1_hits = 0
    top3_hits = 0
    for case in cases:
        ranked = recommend_issue_files(case["issue"], case["tree"])
        paths = [item.path for item in ranked]
        expected = set(case["expected_paths"])
        top1 = bool(paths[:1] and paths[0] in expected)
        top3 = bool(expected.intersection(paths[:3]))
        top1_hits += int(top1)
        top3_hits += int(top3)
        results.append(
            {
                "name": case["name"],
                "top1_hit": top1,
                "top3_hit": top3,
                "ranked_paths": paths[:3],
                "expected_paths": sorted(expected),
            }
        )
    total = len(cases)
    return {
        "case_count": total,
        "candidate_top1_accuracy": top1_hits / total if total else 0,
        "candidate_top3_recall": top3_hits / total if total else 0,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate RepoLens heuristics.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    print(json.dumps(evaluate(cases), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
