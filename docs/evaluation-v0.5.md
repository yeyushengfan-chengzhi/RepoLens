# RepoLens v0.5 Evaluation

Date: 2026-07-20

## Scope

This regression evaluation covers five curated Issue-to-file cases in `evaluation/cases.json`. It measures only the transparent candidate-file heuristic. It is intentionally small and must not be presented as general GitHub accuracy.

## Results

| Metric | Result |
| --- | ---: |
| Cases | 5 |
| Candidate Top-1 accuracy | 100% |
| Candidate Top-3 recall | 100% |
| Backend automated tests | 34 passed |

The first run scored 60% for both retrieval metrics. Two failures showed that exact body-keyword matches were underweighted and that `github` was incorrectly treated as a stop word. After changing those rules, all five fixed regression cases passed.

## Real-model observation

The v0.5 asynchronous check on `open-webui/open-webui#27206` completed in 26.03 seconds with `source=hermes`, two cited evidence snippets, and a structurally complete result. Hermes reported:

| Usage field | Value |
| --- | ---: |
| Prompt tokens | 18,173 |
| Prompt cache-hit tokens | 0 |
| Prompt cache-miss tokens | 18,173 |
| Completion tokens | 1,538 |
| Total tokens | 19,711 |
| Estimated cost using configured `deepseek-v4-flash` rates | ¥0.021249 |

An immediate repeat completed from the persistent cache in 0.057 seconds with no new model request and therefore no new model charge. The cached response retains the original usage for audit purposes.

The cost is an estimate because Hermes may be configured with a different underlying DeepSeek model or provider price. Provider-reported usage is authoritative, and the configured pricing variables must match the account.

## Limitations

- The five cases are synthetic and based on known paths.
- The evaluation does not yet score evidence faithfulness or recommendation quality.
- Live GitHub and DeepSeek evaluations cost network quota and tokens, so they are not part of CI.
- Future evaluation should add at least 20 frozen real Issues with manually reviewed expected files and evidence.

## Reproduce

```powershell
.\.conda\python.exe scripts\evaluate.py
.\.conda\python.exe -m unittest discover -s backend/tests -v
```
