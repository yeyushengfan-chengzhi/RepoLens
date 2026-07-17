# RepoLens SCM Workflow

RepoLens follows an upstream/origin/local workflow adapted for a one-person open-source portfolio project.

## Roles

- Owner (human): choose requirements, approve pull requests, and publish releases.
- Hermes + DeepSeek: refine issues, acceptance criteria, and durable task context.
- Codex: implement changes, run tests, inspect diffs, and review pull requests.
- GitHub Actions: enforce automated quality checks.

## Branches

- `main`: stable, releasable code only.
- `feat/<short-name>`: new features.
- `fix/<short-name>`: bug fixes.
- `docs/<short-name>`: documentation-only changes.
- `chore/sync-upstream-YYYYMMDD`: upstream synchronization.

## Change Flow

1. Create a GitHub issue with scope and acceptance criteria.
2. Update local `main` from `origin/main`.
3. Create a typed change branch.
4. Make focused changes and run relevant tests.
5. Inspect `git status` and stage selected files; do not blindly commit secrets.
6. Commit using Conventional Commits, for example `feat: analyze repository metadata`.
7. Push the branch and open a pull request.
8. Pass CI, Codex review, and owner approval.
9. Squash merge into `main`.
10. Create a version tag and GitHub Release for public milestones.

## Upstream Synchronization

Do not merge upstream changes directly into stable `main`. Fetch the upstream repository, create a `chore/sync-upstream-YYYYMMDD` branch, resolve conflicts there, run tests, and merge through a pull request.

