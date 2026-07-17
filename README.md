# RepoLens

RepoLens is a GitHub repository understanding and issue triage assistant built with Open WebUI, Hermes Agent, DeepSeek, FastAPI, and Codex.

## Current Status

- Open WebUI runs locally at `http://127.0.0.1:3000`.
- Hermes Agent exposes an OpenAI-compatible API at `http://127.0.0.1:8642/v1`.
- Hermes uses the existing DeepSeek provider configuration.
- The RepoLens dashboard runs at `http://127.0.0.1:8000`.
- The first working feature analyzes a public GitHub repository, its languages, root directory, and open issues.
- Issue recommendations use transparent label and complexity heuristics; they are not LLM guesses.

## Local Services

After stopping the services that were started manually, future launches can use:

```powershell
powershell -ExecutionPolicy Bypass -File E:\RepoLens\scripts\start-repolens.ps1
```

To stop services launched by that script:

```powershell
powershell -ExecutionPolicy Bypass -File E:\RepoLens\scripts\stop-repolens.ps1
```

Runtime logs and PID metadata are written to `.runtime/` and are ignored by Git.

## First Feature

Open `http://127.0.0.1:8000`, enter a public repository URL, and select **Analyze repository**. Authentication is optional for early local testing. Set `GITHUB_TOKEN` in the process environment later if the anonymous GitHub API rate limit becomes restrictive.

## Architecture

```text
Open WebUI (browser)
    -> Hermes Agent API
        -> DeepSeek API
    -> RepoLens FastAPI service (planned integration)
        -> GitHub API
        -> repository analyzer
        -> issue triage workflow
```

## Roadmap

1. Accept and validate a public GitHub repository URL.
2. Fetch repository metadata, directory structure, and open issues.
3. Rank issues by difficulty and newcomer suitability.
4. Generate evidence-linked implementation and test plans.
5. Add caching, tests, CI, a demo repository, and evaluation cases.

## Security

Secrets remain in the existing Hermes configuration and are never copied into this repository. Do not commit `.env`, `config.yaml`, local databases, or runtime logs.
