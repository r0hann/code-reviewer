# Claude Code Tooling for Automation / Agentic AI Work

Reviewed by: Automation Software Engineer (Claude)
Date: 2026-07-29
Scope: what skills, commands, rules, instruction files, and prompts exist on
this machine (or are installable) for building/maintaining automation and
agentic-AI workflows — surveyed while building the Groq/LangGraph PR
reviewer in this repo. Reference: https://code.claude.com/docs/en/memory

## 1. How Claude Code persists instructions (per the memory docs)

Two separate systems, both loaded into context at session start:

| | CLAUDE.md files | Auto memory |
|---|---|---|
| Who writes it | You | Claude |
| Contains | Instructions/rules | Learnings/patterns |
| Scope | Project, user, or org | Per repo, shared across worktrees |
| Loaded into | Every session, in full | Every session, first 200 lines / 25KB of `MEMORY.md` |

- **CLAUDE.md locations, load order (broadest → most specific):** managed
  policy file (org-wide, IT-deployed) → `~/.claude/CLAUDE.md` (your personal,
  all projects) → `./CLAUDE.md` or `./.claude/CLAUDE.md` (project, shared via
  git) → `./CLAUDE.local.md` (personal, project-specific, gitignored).
- **`.claude/rules/`** — split large CLAUDE.md content into topic files; can
  be scoped to file paths via `paths:` frontmatter so a rule only loads when
  Claude touches matching files (e.g. only load API rules when editing
  `src/api/**`).
- **Auto memory** — lives at
  `~/.claude/projects/<project>/memory/MEMORY.md` (+ topic files), keyed off
  the git repo so it's shared across worktrees but not across machines.
  Claude decides what's worth saving; you can browse/edit it with `/memory`.
- **`AGENTS.md`** — Claude Code doesn't read it directly; import it from
  CLAUDE.md with `@AGENTS.md` if the repo already has one for other agents.
- Neither system is a hard enforcement layer — for anything that must run
  every time regardless of what Claude decides, use a **hook**
  (`PreToolUse`/`PostToolUse`/`Stop`), not CLAUDE.md text.

## 2. Current state of this project

- No project `CLAUDE.md` or `.claude/CLAUDE.md` yet.
- No `.claude/rules/` (user or project level).
- No custom slash commands (`~/.claude/commands/` and project
  `.claude/commands/` are both empty).
- Auto memory directory exists for this project
  (`~/.claude/projects/-Users-rohan-Desktop-Project-Automation-Code-Reviewer/memory/`)
  but is currently empty — nothing saved yet.
- Only `.claude/settings.local.json` exists in this repo (local permission
  settings), nothing automation-specific.

## 3. Built-in skills already available (no install needed)

These are bundled with Claude Code itself and were usable in this session
without enabling any plugin — several are directly relevant to
automation/agentic work:

| Skill | Relevance to automation/agentic AI |
|---|---|
| `/schedule` | Create/manage cron-scheduled cloud agents ("routines") — recurring agentic runs |
| `/loop` | Run a prompt or slash command on a recurring interval, self-paced or fixed |
| `/run` | Launch and drive an app to verify an automation actually works end-to-end |
| `/verify` | Exercise a change end-to-end rather than trusting tests alone |
| `/code-review`, `/security-review`, `/review`, `/simplify` | The categories of review this repo's own Groq/LangGraph bot is trying to replicate |
| `/init` | Generate/refresh a project `CLAUDE.md` from the codebase |
| `/update-config` | Configure hooks, permissions, env vars — the mechanism for wiring real automation into settings.json |

## 4. Marketplace plugins relevant to automation/agentic AI (cached, not currently enabled)

`claude-plugins-official` is a known marketplace on this machine
(`~/.claude/plugins/marketplaces/claude-plugins-official`), but
`enabledPlugins` in `~/.claude/settings.json` is empty — none of these are
active yet. They'd need `/plugin install <name>` (or equivalent) to use.

| Plugin | What it gives you | Why it matters here |
|---|---|---|
| **claude-code-setup** | `claude-automation-recommender` skill — analyzes a codebase and recommends hooks/subagents/skills/plugins/MCP servers tailored to it | Could have generated a version of this same review automatically |
| **hookify** | `/hookify` command + `writing-rules` skill — turn plain-English rules ("always X", "never Y") into enforced hooks | Direct match for "rule" in your ask — this is the tool for turning instructions into enforced automation instead of just CLAUDE.md text |
| **ralph-loop** | Interactive self-referential loop ("Ralph Wiggum technique") — Claude repeatedly works the same task, seeing its prior output, until done | Agentic-loop pattern; useful for autonomous multi-pass work, distinct from `/loop`'s interval-based repetition |
| **agent-sdk-dev** | Dev kit for building your own agents with the Claude Agent SDK | Relevant if you want to build the reviewer as a standalone agent app instead of a single-shot GitHub Action script |
| **pr-review-toolkit** | Multiple specialized PR-review agents (comments, tests, error handling, type design, simplification) with confidence scoring | Closest existing analog to what `github_reviewer.py` does — worth comparing approaches |
| **feature-dev** | Multi-agent feature workflow: exploration → architecture → quality review | General agentic-workflow reference pattern |
| **mcp-server-dev** | Skills for building MCP servers (tool integrations) | Relevant if the reviewer should call out to other tools (e.g. a linter, a ticketing system) |
| **claude-md-management** | Audit CLAUDE.md quality, capture session learnings, keep project memory current | Directly automates what section 2 above shows this repo is missing |
| **security-guidance** | Pattern-based warnings + LLM diff review + agentic commit reviewer for vulnerability classes (injection, XSS, SSRF, hardcoded secrets, etc.) | Overlaps with what you might want the Groq reviewer to catch |

## 5. Recommendations for this repo

1. Run `/init` to generate a project `CLAUDE.md` — right now there's no
   persistent project-level instruction file, so every new session starts
   with zero project context beyond what's in this repo's files.
2. If you want review-style rules ("flag hardcoded secrets", "always check
   `runs-on` not `runs-with`") to be *enforced* rather than just suggested,
   look at the **hookify** plugin — that's the tool built for turning rules
   into hooks, which run regardless of what the model decides.
3. Consider running the **claude-automation-recommender** skill
   (`claude-code-setup` plugin) once this repo has more code in it — it's
   built exactly for "what automation should I set up here" and would
   complement this manual review with a codebase-driven one.
4. None of the above requires touching `github_reviewer.py` or the
   workflow — this is tooling for *your* Claude Code setup, separate from
   the GitHub Action reviewer already built in this repo (see
   `PLAN_REVIEW.md`).
