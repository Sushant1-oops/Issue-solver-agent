# Issue Solver

An agentic AI that takes a **GitHub issue**, reads the repository, **writes the fix**, **runs the tests**, and **opens a pull request** — with a live React dashboard streaming every step.

```
Issue URL ──▶ FastAPI + LangGraph agent ──▶ PR opened
              │
              ├─ GitHub API (issue, comments, base branch, write-access probe)
              ├─ fork upstream first if you cannot push to it
              ├─ git shallow clone + ai-fix branch
              ├─ local codebase index (no embeddings)
              ├─ LLM call 1: locate relevant files
              ├─ regex grep + capped file reads
              ├─ LLM call 2: search/replace patches
              ├─ pytest / npm test (auto-detected, timed)
              ├─ LLM call 3 (only if tests fail): repair
              └─ commit → push (to your fork when forked) → POST /pulls
```

- **Backend**: FastAPI, LangGraph, LangChain, httpx, Git CLI
- **Frontend**: React 19 + Vite 7 + Tailwind CSS v4 (TypeScript)
- **Models**: MiniMax **M3.1 Flash (free)** via OpenRouter as primary, the **Gemini** free tier as fallback. Both are ordered chains of model names, tried in sequence.

---

## Quick start

### 1. Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env          # paste your keys, save
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload-dir app
```

> Reload note: keep `--reload-dir app`. A bare `--reload` also watches `backend/workspace/`, where every job clone lives, so an in-flight job would restart the server.

### 2. Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173** (the Vite dev server proxies `/api` to port 8000).

To confirm everything is wired up: `http://127.0.0.1:8000/api/health` should report both providers as `configured: true`.

---

## Verify the agent without API keys

`backend/tests_offline.py` runs the **real** LangGraph wiring, patcher, git branch/commit and pytest against a throwaway repository, faking only the LLM and the GitHub/git network calls (no request leaves the machine, even if real tokens are in `.env`). It exercises the repair loop end to end (bad fix → failing tests → repair → passing tests → PR) and runs **four submission routes**:

| Scenario | Setup | Proves |
| --- | --- | --- |
| `direct` | token can push to the issue's repo | branch pushed there, PR head is a bare branch name |
| `forked` | no push right, forking allowed | upstream forked, fork cloned and pushed, PR opened **against the upstream** with head `you:branch` |
| `preview` | nothing writable | fix is still committed and tested locally, nothing pushed, no PR |
| `selffork` | `PREFER_FORK` on a repo you own | GitHub's "fork" echo is detected, the run degrades to a direct push and never claims a fork |

Two more groups drive the failure paths directly: **llm fallback** (a retired `404` model name falls through to the next candidate; an exhausted chain reports every attempt; a rate-limited primary is skipped while it cools down) and **test environment** (an unimportable suite is reported as unverified, while a failure raised inside a collected test stays a failure).

```powershell
cd backend
.\.venv\Scripts\python.exe tests_offline.py
```

Exit code 0 with every line `PASS` means the pipeline is healthy.

## Try it on a throwaway repo

To exercise the full issue → PR loop against a real repository without touching a
project you care about, create an **empty** repo on GitHub (a fine-grained token
cannot create repos), then seed it with one bug, a failing test suite and an issue:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\seed_demo_repo.py your-name/some-demo-repo
```

The script pushes the buggy `calculator.py` plus failing `tests/test_calculator.py`
to the default branch and opens an issue describing the symptom, then prints the
`repo_url` / issue number to submit. Re-run it any time to reset the demo — the
seeded bug (`celsius_to_fahrenheit` using `- 32`) is one the free models fix reliably,
so the run usually lands in **well under a minute with a +1/-1 diff**.

---

## Configuration (`backend/.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | — | Primary provider key |
| `PRIMARY_MODEL` | `minimax/minimax-m3.1-flash:free` | Free MiniMax model on OpenRouter; comma-separated candidates are tried in order |
| `GEMINI_API_KEY` | — | Fallback provider key |
| `GEMINI_MODEL` | `gemini-flash-lite-latest,gemini-2.5-flash` | Free-tier Gemini model chain — newer accounts get `404` on the pinned `gemini-2.5*` names |
| `GITHUB_TOKEN` | — | Fine-grained PAT: reads issues, pushes to repos you own |
| `GITHUB_CLASSIC_TOKEN` | — | Classic PAT with `repo` scope — **required to fork someone else's repo** (preferred for every call when set) |
| `ALLOW_FORK` | `true` | Fork a repository you cannot push to, so a cross-repo PR can still be opened |
| `PREFER_FORK` | `false` | Take the fork route even when the repo could be pushed to directly — useful when you already own a fork of the project. GitHub refuses to fork a repo you own, so for your own repos this falls back to a direct push |
| `OVERALL_TIMEOUT_S` | `240` | Hard ceiling per job |
| `MAX_FILE_CHARS` / `MAX_FILES_READ` | `14000` / `3` | Context caps per file / per prompt |
| `REPO_MAP_MAX_ENTRIES` | `300` | Largest file map shown to the model |
| `TEST_TIMEOUT_S` / `INSTALL_TIMEOUT_S` | `75` / `60` | Test and dependency budgets |

**GitHub token scopes** — both kinds are supported at the same time and the classic one wins for every read and write, because it is the only kind that can create a fork.

- **Classic PAT** (`https://github.com/settings/tokens/new?scopes=repo`) → put it in `GITHUB_CLASSIC_TOKEN`. It can read issues, push branches, open PRs, create repositories **and fork other people's repositories** — everything the agent does. Add `workflow` if a target repo keeps CI files under `.github/workflows/`.
- **Fine-grained PAT** → put it in `GITHUB_TOKEN` with *Contents: Read and write*, *Issues: Read and write*, *Pull requests: Read and write*. It is enough for repositories you own or can push to, but note the limits seen in practice:
  - it **cannot create repositories or forks** (`POST /user/repos` and `POST /repos/{owner}/{repo}/forks`
    both answer `403 Resource not accessible by personal access token`), so an issue in a repository
    you cannot push to stops at a local commit unless a classic token is configured;
  - a repo created *after* the token was issued is invisible to a token scoped to *Only selected repositories* (`404`, not `403`).

Public issues can be read without a token, and the fix is still produced and tested — only the push and PR are skipped. For org repos with SAML SSO, click **Configure SSO** next to the token and authorize it for the organization, otherwise pushes fail with `400 Requires authentication`.

### Solving an issue in someone else's repository

Paste any issue URL you can read. Before spending an LLM call the agent asks GitHub what the
authenticated account may do (`/user` + the repo's `permissions`/`allow_forking`) and picks a route:

1. **push allowed** → branch and PR land in that repo directly.
2. **no push, forking allowed** → `POST /repos/{owner}/{repo}/forks` (needs the classic `repo`
   scope), the fork is polled until usable (`FORK_WAIT_TIMEOUT_S`, default 60s) and synced with
   `merge-upstream` when possible, then the **fork** is cloned, fixed, tested and pushed, and the
   pull request is opened **against the upstream repo** with the head written as
   `your-login:ai-fix/issue-N-…`. Nothing is ever written into the other party's repository — the
   maintainer still decides whether to merge.
3. **neither** → the fix is committed and tested locally and reported in the UI, with no push.

**GitHub will not fork a repository into the account that already owns it.** `POST /repos/{owner}/{repo}/forks`
answers `200` with the **upstream's own** `full_name` in that case (the `name` parameter is ignored), so
`PREFER_FORK=true` cannot demonstrate the cross-repo path using two of your own repos. The agent
detects that echo, raises instead of pretending, logs `No separate fork was created (…) Falling back
to pushing the branch directly to owner/repo`, and opens a normal same-repo pull request — the
`selffork` scenario in `tests_offline.py` asserts exactly that, including that the PR is **not**
labelled as coming from a fork.

What `PREFER_FORK=true` is genuinely for: repositories you *can* push to but would rather contribute
to through your existing fork (a project you already forked, an org repo where maintainers expect
PRs from forks). If a fork of the upstream already exists under your account, that endpoint returns
it and the full fork → fix → test → cross-repo PR path runs.

The only way to see a brand-new fork being created is to point the agent at a repository owned by
**someone else** that you can read and that allows forking.

Extra API calls on the fork path: one fork request plus a few polls, typically **5–15 seconds** —
inside the 240s job budget. Rate limits: an account cannot create more than ~10 forks per hour
(GitHub returns `403 You have exceeded too many forks`), and a repo can only be forked once per
account, so the existing fork is reused and synced instead.

> Free-model note: OpenRouter lists free variants with a `:free` suffix and daily/RPM caps. If `minimax/minimax-m3.1-flash:free` is unavailable for your account, pick another free MiniMax slug at [openrouter.ai/models](https://openrouter.ai/models) and set `PRIMARY_MODEL`.

---

## How the 2–3 minute and rate-limit targets are met

**Only 2–3 LLM calls per issue.**
1. `locate` — issue + file map → candidate paths and regex queries
2. `apply_fix` — issue + capped file contents → search/replace patches
3. `apply_fix` (repair) — *only when tests fail*, using the test output tail

No embeddings, no vector database, no agent chatter loop: the codebase is walked locally, grep is done with the Python `re` module, and file reads are truncated around a fixed character budget.

**Measured per-stage budgets** (all enforced as timeouts, so a heavy repo degrades instead of hanging):

| Stage | Budget |
| --- | --- |
| Shallow clone / push | `CLONE_TIMEOUT_S` 90s |
| Dependency install for tests | `INSTALL_TIMEOUT_S` 60s (best effort) |
| Test run | `TEST_TIMEOUT_S` 75s |
| Each LLM call | `LLM_TIMEOUT_S` 120s |
| **Whole job** | `OVERALL_TIMEOUT_S` 240s |

A typical small/medium repo completes in **60–150 seconds**.

**Prompt size caps** keep tokens predictable: repo map ≤ 6 KB, issue body ≤ 3.5 KB, at most 3 files × 14 KB, search results ≤ 5 KB, test output tail ≤ 3.5 KB.

**Rate-limit handling**: on a `429` from OpenRouter the primary provider goes on a 10-minute cooldown and every remaining call in the job (and other jobs) is routed to Gemini automatically. Model output is demanded as a single JSON object; `</think>` reasoning spillover, markdown fences, and trailing commas are all tolerated, with one automatic "return valid JSON" repair round.

---

## API

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/health` | Provider/model/token status (never leaks key material) |
| `POST` | `/api/jobs` | `{ "repo_url": "...", "issue_number": 12 }` → starts a job |
| `GET` | `/api/jobs` | Recent runs with status and duration |
| `GET` | `/api/jobs/{id}` | Single run summary |
| `GET` | `/api/jobs/{id}/events` | **SSE** progress stream (`step`, `issue`, `log`, `patch`, `test`, `pr`, `done`, `error`) |

The event log is stored per job and replayed from index 0, so reloading the dashboard rebuilds a finished run exactly.

---

## Repository layout

```
backend/
  tests_offline.py   keyless end-to-end run of the graph (see above)
  scripts/
    seed_demo_repo.py  seeds a throwaway repo with a bug + failing tests + an issue
  app/
    main.py          FastAPI app, CORS, /api/health
    config.py        env-driven settings
    graph.py         LangGraph nodes, retry loops, PR body
    state.py         AgentState schema
    jobs.py          job store + runner + event fan-out
    llm.py           provider fallback + JSON extraction
    prompts.py       locate/fix system prompts
    github_api.py    issue read, repo info, PR create
    gitops.py        clone, branch, commit, push (redacts the token)
    codebase.py      file map, capped reads, regex search
    patcher.py       patch application with fuzzy fallback
    testrunner.py    pytest/npm detection, venv setup, execution
    routers/jobs.py  REST + SSE endpoints
  workspace/         per-job clones (gitignored, safe to delete)
frontend/
  src/
    App.tsx, api.ts, types.ts
    hooks/useJobStream.ts
    components/  IssueForm, Timeline, LogPanel, PatchPanel, TestPanel,
                 ResultCard, JobHistory, HealthChips, StatusBadge
    lib/         diff.ts (line diff), utils.ts
```

---

## Behavior details

- **Isolation**: each job clones into `backend/workspace/<job-id>` on branch `ai-fix/issue-<n>-<id>`. Nothing touches your local checkout; delete `workspace/` any time.
- **Patches are search/replace**, not whole-file rewrites — cheaper and safer. An exact match is tried first, then a whitespace-insensitive line window. Path traversal (`../`) is rejected.
- **Commit hygiene**: `.venv/` and `node_modules/` are added to `.git/info/exclude` right after cloning so generated artifacts never land in the PR.
- **Explicit git identity**: pushes use the configured token only (`GITHUB_CLASSIC_TOKEN` when set), with credential helpers disabled and `GIT_TERMINAL_PROMPT=0`. The agent can never silently push under a login cached in your machine's credential manager, and it fails fast instead of waiting on a prompt. Tokens are redacted from any command output shown in the UI.
- **Tests**: pytest runs with `-x -q` (stop at first failure). Every requirements file the repo keeps — `requirements.txt`, `dev-requirements.txt`, `requirements-dev.txt`, `test-requirements.txt`, `requirements/{dev,tests,test}.txt` — is installed in one pip call, since test dependencies rarely live in the base file. pytest exit code 5 (nothing collected) and failed dependency installs are reported as *skipped*. A suite that cannot even be imported (`ImportError while loading conftest`, collection errors, a bare `ModuleNotFoundError` at import time) is reported as **unverified** with the missing package named, so a broken environment never spends the repair round and never looks like a failing test. An error raised *inside* a collected test remains a real failure.
- **Unresolved test failures**: after one repair round the PR is still opened, but the description states that tests are failing, so nothing is silently hidden.
- **PR body** contains the model's fix summary, the issue link (`Fixes #n`), test status, and the changed-file list.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `LLMError: No LLM provider configured` | Keys missing — fill `backend/.env` and restart uvicorn |
| `All N LLM attempts failed … 404 … no longer available to new users` | That model name is retired for your account. It is no longer fatal when a chain is configured: each `provider/model` is tried in order. Check what your key may call: `GET https://generativelanguage.googleapis.com/v1beta/models?key=…` |
| `All N LLM attempts failed … 503 … high demand` or `provider returned an empty response` | The model is saturated or answered with only reasoning. The last candidate is retried once after 2s, then the next model in the chain takes over; add another `GEMINI_MODEL`/`PRIMARY_MODEL` candidate if it persists |
| `All LLM providers failed … 429` | Both free tiers rate-limited; wait, or raise quotas. A 429 from OpenRouter puts it on a 10-minute cooldown so the rest of the job runs on Gemini |
| `git clone … failed` | Private repo needs `GITHUB_TOKEN`; or `git` not on PATH |
| `pushing branch … failed` / PR API 422 | Token lacks write scope. Since the agent now forks automatically, `Permission to owner/repo.git denied to …` should no longer appear — if it does, forking was off (`ALLOW_FORK=false`) or the upstream disallows forks, so point `GITHUB_CLASSIC_TOKEN` at a token with the `repo` scope |
| `Could not fork owner/repo (HTTP 403)` | The active token is fine-grained (it cannot create forks). Set `GITHUB_CLASSIC_TOKEN` to a classic PAT with the `repo` scope |
| `Fork owner/repo was not ready within 60s` | GitHub creates forks asynchronously and was slow; raise `FORK_WAIT_TIMEOUT_S` and rerun |
| `403 Must have admin rights to Repository` when deleting a fork | A `repo`-scoped classic token can create a fork but not delete one — tick **`delete_repo`** when minting it, or delete the fork in the web UI (its Settings page → *Danger Zone* → *Delete this repository*) |
| `No pull request was opened` + `this run cannot write to GitHub` | The chosen route ended in `preview`: no token, forking disabled, or the repo forbids forks. The fix and test result are still shown in the UI |
| `remote: Write access to repository not granted` | The token is **read-only for code**. Fine-grained: set *Contents: Read and write* (+ *Issues* and *Pull requests* read/write). Classic: the `repo` scope is missing. The Contents API fails the same way with `403 Resource not accessible by personal access token` |
| `The fix is still produced and tested locally, but no pull request can be opened` | The route resolved to `preview` and no token is set: the fix and test result are still shown, only the push and PR are skipped |
| `Tests skipped` | Repo has neither a `tests/` directory with pytest files nor an npm `test` script |
| `UNVERIFIED` + `The suite could not be imported (missing: <pkg>)` | pytest named the module it could not import, so the test dependencies are still missing. Check that the repo's dev/test requirements file is one of the names listed under *Tests* above, or add it to `REQ_FILES` in `testrunner.py` |
| Frontend shows "backend offline" | uvicorn not running on port 8000 |
| Job aborts at 240s | Large repo: raise `OVERALL_TIMEOUT_S`, or narrow the issue |

## Limitations

- Works best on small/medium repos with a runnable test suite; a missing or very slow suite is skipped rather than blocking the PR.
- A cross-repo contribution can only ever be a **pull request against the upstream** — the agent never writes to a repository you cannot push to, and the maintainer still reviews and merges. Forking itself requires a classic `repo` token; with only a fine-grained token, foreign issues stop at a tested local commit.
- The agent reads at most 3 candidate files per round to protect the token budget — very large refactors are out of scope.
- Free-tier models are noticeably weaker than paid coding models: expect correct small bug fixes and rough patches on subtle issues. Always review the generated PR.
- Cloned code is executed locally by the test runner; only point the tool at repositories you trust.
