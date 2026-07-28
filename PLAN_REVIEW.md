# AI Code Reviewer — Plan Review & Refinement Log

Reviewed by: Automation Software Engineer (Claude)
Date: 2026-07-29

## 1. Original Plan Summary

Build a GitHub Action that, on every PR open/push, fetches the diff via PyGithub,
sends it to Groq's free-tier LLM API through a small LangGraph graph
(`analyzer` -> `publisher`, with one retry on failure), and posts the result as
a PR comment. Setup was: create a Groq API key, store it as `GROQ_API_KEY` in
GitHub Actions secrets, add `github_reviewer.py` at the repo root, and add
`.github/workflows/ai_code_review.yml`.

The overall approach (Actions + PyGithub + a free LLM, one retry on failure)
is sound and cheap to run. It did not work as submitted — two bugs stop it
from executing at all, and several gaps would cause it to misbehave silently
on real PRs.

## 2. Issues Found

### Blocking (script/workflow cannot run)

| # | Issue | Where | Effect |
|---|---|---|---|
| 1 | Malformed `return` — `"error":` has no value and the dict/parens never close | `analyze_code_node` | `SyntaxError` at import; the script never runs |
| 2 | `runs-with: ubuntu-latest` — invalid key, should be `runs-on` | workflow YAML, `run-reviewer` job | Job fails to schedule; workflow never starts |

### Robustness gaps (runs, but degrades or misbehaves on real PRs)

| # | Issue | Effect |
|---|---|---|
| 3 | `file.patch` can be `None` for binary or very large diffs (GitHub omits it) | The literal string `"None"` gets concatenated into the prompt payload |
| 4 | No cap on diff payload size | A large PR can exceed the model's context window (`llama3-8b-8192` = 8192 tokens), causing the call to fail |
| 5 | On repeated failure (both retries exhausted), the script still posts a comment, using the empty default `review_comments` | Users see a blank "AI Code Reviewer" comment with no explanation instead of an error message |
| 6 | Hardcoded model name `llama3-8b-8192` | Groq's free-tier model lineup changes over time; this id may be deprecated by the time it's deployed and there was no way to change it without editing code |
| 7 | A new comment is created on every `synchronize` event (every push to the PR) | Comment spam on active PRs — no way to see just the latest review inline |
| 8 | Sending PR diffs to a third-party API (Groq) | Acceptable for personal/OSS repos; flagged as a call the user should make consciously for anything proprietary — not changed in code, just noted |

## 3. Changes Made

Implemented in `github_reviewer.py` and `.github/workflows/ai_code_review.yml`:

1. **Fixed the syntax error** — `analyze_code_node` now returns
   `{"review_comments": ..., "iterations": ..., "error": ""}` on success.
2. **Fixed `runs-with` → `runs-on`** in the workflow YAML.
3. **Guarded against `file.patch is None`** — such files are now noted as
   `(diff omitted by GitHub - binary or too large)` instead of inserting the
   string `"None"` into the prompt.
4. **Added a payload size cap** (`MAX_PAYLOAD_CHARS = 12000`) — the combined
   diff is truncated with an explicit `[... diff truncated ...]` marker rather
   than silently overflowing the model's context window.
5. **Added a graceful failure path** — if both attempts fail, the posted
   comment says the reviewer failed and includes the last error, instead of
   posting an empty comment.
6. **Made the model configurable** — `GROQ_MODEL` env var, defaulting to
   `llama3-8b-8192`. *Action needed from you: confirm this model id is still
   live on the [Groq console](https://console.groq.com/docs/models) before
   relying on it — free-tier model availability changes over time.*
7. **Switched to a sticky comment** — the script now searches the PR's issue
   comments for a hidden marker (`<!-- ai-code-reviewer:groq-langgraph -->`)
   left by a previous run and edits that comment in place on subsequent
   pushes, instead of creating a new one each time.
8. **Restructured into functions** (`build_llm`, `build_graph`,
   `gather_changes`) called from `__main__` — same behavior, but testable in
   isolation and no top-level side effects (API client construction, etc.) on
   import.
9. Left untouched: the two-node LangGraph structure, the retry-once policy,
   the file-extension allowlist, the secrets setup steps, and the manual test
   plan (test branch with a deliberately buggy file → open PR → check Actions
   tab) — all of that was already correct.

## 4. What Was *Not* Changed (and why)

- **Data-privacy concern (#8)** — this is a decision only you can make
  (personal/OSS repo vs. proprietary code); no code change applies here,
  just the flag above.
- **Inline, line-anchored review comments** (vs. one summary comment) —
  out of scope for this pass; the original plan only asked for a summary
  comment and this keeps the diff minimal. Worth a follow-up if you want
  comments anchored to specific diff lines (`create_review` with
  `create_review_comment` instead of `create_issue_comment`).
- **Multi-file / chunked review for very large PRs** — the truncation in
  change #4 is a safety net, not a real solution for huge PRs. A proper fix
  (map-reduce over files) is more effort than this plan asked for; flagging
  it as a known limitation rather than building it speculatively.

## 5. Setup Steps (unchanged from original plan)

1. Create a free Groq API key at the Groq Console.
2. In your GitHub repo: Settings → Secrets and variables → Actions → New
   repository secret → name it `GROQ_API_KEY`, paste the key.
3. Files are already in place in this repo:
   - `github_reviewer.py`
   - `.github/workflows/ai_code_review.yml`
4. (Optional) Set a `GROQ_MODEL` repo/workflow variable if you want to pin a
   specific Groq model instead of the default `llama3-8b-8192`.

## 6. Test Plan (unchanged from original plan)

1. Commit and push `github_reviewer.py` and the workflow file to `main`.
2. Create a branch, add a file with an intentional bug, push it.
3. Open a PR from that branch into `main`.
4. Watch the Actions tab — the job should install deps, call Groq, and post
   (or update) a PR comment starting with `### \U0001F916 LangGraph AI Code Reviewer`.
5. Push a second commit to the same PR and confirm the *existing* comment is
   edited in place rather than a new one being added (verifies change #7).
