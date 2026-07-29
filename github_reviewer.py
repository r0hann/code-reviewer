import json
import os
import re
import sys

from github import Github
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

# Marker used to find and update our own fallback comment instead of
# spamming a new one on every push to the PR. Inline review comments
# (the happy path) intentionally create a fresh review each run, same
# as any other GitHub review bot.
COMMENT_MARKER = "<!-- ai-code-reviewer:groq-langgraph -->"

# Rough char budget so the diff payload stays inside the model's context
# window alongside the system/instruction prompt and the model's reply.
MAX_PAYLOAD_CHARS = 40000

REVIEWED_EXTENSIONS = (".py", ".js", ".ts", ".go")

HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

# Checked in order; the first one whose API key env var is set wins,
# unless LLM_PROVIDER is set explicitly to force a specific provider.
PROVIDER_KEY_ENV_VARS = [
    ("groq", "GROQ_API_KEY"),
    ("anthropic", "ANTHROPIC_API_KEY"),
    ("openai", "OPENAI_API_KEY"),
    ("google", "GOOGLE_API_KEY"),
    ("openrouter", "OPENROUTER_API_KEY"),
]


class ReviewState(TypedDict):
    code_changes: str
    valid_lines: dict
    review_comments: str
    iterations: int
    error: str


def detect_provider():
    explicit = os.environ.get("LLM_PROVIDER")
    if explicit:
        return explicit.strip().lower()

    for provider, key_env_var in PROVIDER_KEY_ENV_VARS:
        if os.environ.get(key_env_var):
            return provider

    configured_keys = ", ".join(k for _, k in PROVIDER_KEY_ENV_VARS)
    raise ValueError(
        f"No LLM API key found. Set one of: {configured_keys} "
        "(or set LLM_PROVIDER explicitly if you have more than one configured)."
    )


def build_llm():
    provider = detect_provider()

    if provider == "groq":
        from langchain_groq import ChatGroq

        model_name = os.environ.get("GROQ_MODEL") or "llama-3.1-8b-instant"
        return ChatGroq(
            model=model_name,
            temperature=0.0,
            groq_api_key=os.environ["GROQ_API_KEY"],
            model_kwargs={"response_format": {"type": "json_object"}},
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        model_name = os.environ.get("ANTHROPIC_MODEL") or "claude-haiku-4-5-20251001"
        return ChatAnthropic(
            model=model_name,
            temperature=0.0,
            anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        model_name = os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
        return ChatOpenAI(
            model=model_name,
            temperature=0.0,
            api_key=os.environ["OPENAI_API_KEY"],
            model_kwargs={"response_format": {"type": "json_object"}},
        )

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        model_name = os.environ.get("GOOGLE_MODEL") or "gemini-1.5-flash"
        return ChatGoogleGenerativeAI(
            model=model_name,
            temperature=0.0,
            google_api_key=os.environ["GOOGLE_API_KEY"],
        )

    if provider == "openrouter":
        from langchain_openai import ChatOpenAI

        model_name = os.environ.get("OPENROUTER_MODEL")
        if not model_name:
            raise ValueError("OPENROUTER_MODEL must be set when using the openrouter provider.")
        return ChatOpenAI(
            model=model_name,
            temperature=0.0,
            api_key=os.environ["OPENROUTER_API_KEY"],
            base_url="https://openrouter.ai/api/v1",
        )

    raise ValueError(f"Unknown LLM_PROVIDER: {provider!r}")


def annotate_patch(patch):
    """Render a unified diff patch with new-file line numbers, and return
    the set of line numbers that are valid to comment on (added or
    unchanged lines, which exist in the new version of the file)."""
    annotated_lines = []
    valid_lines = set()
    new_line = None

    for raw_line in patch.splitlines():
        header_match = HUNK_HEADER_RE.match(raw_line)
        if header_match:
            new_line = int(header_match.group(1))
            annotated_lines.append(raw_line)
            continue

        if new_line is None or raw_line.startswith("\\"):
            annotated_lines.append(raw_line)
            continue

        if raw_line.startswith("+"):
            annotated_lines.append(f"{new_line:>5}: {raw_line}")
            valid_lines.add(new_line)
            new_line += 1
        elif raw_line.startswith("-"):
            annotated_lines.append(f"     : {raw_line}")
        else:
            annotated_lines.append(f"{new_line:>5}: {raw_line}")
            valid_lines.add(new_line)
            new_line += 1

    return "\n".join(annotated_lines), valid_lines


def gather_changes(pr):
    changes_payload = ""
    valid_lines_by_file = {}

    for file in pr.get_files():
        if not file.filename.endswith(REVIEWED_EXTENSIONS):
            continue
        if not file.patch:
            # Binary files or diffs GitHub considers too large omit `patch`.
            changes_payload += f"\nFile: {file.filename}\n(diff omitted by GitHub - binary or too large)\n"
            continue

        annotated, valid_lines = annotate_patch(file.patch)
        valid_lines_by_file[file.filename] = sorted(valid_lines)
        changes_payload += f"\nFile: {file.filename}\n{annotated}\n"

    if len(changes_payload) > MAX_PAYLOAD_CHARS:
        changes_payload = (
            changes_payload[:MAX_PAYLOAD_CHARS]
            + "\n\n[... diff truncated to fit model context window ...]"
        )

    return changes_payload, valid_lines_by_file


def analyze_code_node(state: ReviewState):
    current_iter = state.get("iterations", 0)
    print(f"Running Analysis Loop - Iteration {current_iter + 1}")

    prompt = f"""You are an elite senior software engineer auditing a GitHub Pull Request.
Review the code changes below for security flaws, performance bugs, and bad practices.

Each file's diff is annotated with the line number it will have in the FINAL file,
shown before each added ('+') or unchanged line. Only cite line numbers that are shown
in the annotation, and only flag lines that start with '+' (the actual changes) -
never flag unchanged context lines or removed ('-') lines.

Code to Review:
{state['code_changes']}

Respond with ONLY a single JSON object (no markdown fences, no extra text) matching this schema:
{{
  "summary": "1-2 sentence overall summary, or exactly 'LGTM' if there are no issues",
  "findings": [
    {{
      "file": "path/to/file.py",
      "line": 42,
      "severity": "high | medium | low",
      "issue": "short description of the specific problem on that line",
      "fix_prompt": "A self-contained, copy-paste-ready instruction someone could give an AI coding assistant to fix exactly this issue - include the file, the problem, and the desired fix.",
      "suggestion": "The exact replacement code for this single line, preserving original indentation, ready to drop into a GitHub suggested-change block. Use an empty string to suggest deleting the line entirely. Omit this field if the fix can't be expressed as a single-line replacement."
    }}
  ]
}}
If there are no issues, return an empty "findings" list.
"""

    try:
        llm = build_llm()
        response = llm.invoke(prompt)
        return {
            "review_comments": response.content,
            "iterations": current_iter + 1,
            "error": "",
        }
    except Exception as e:
        return {"error": str(e), "iterations": current_iter + 1}


def parse_review_json(raw_text):
    text = raw_text.strip()
    fence_match = re.match(r"^```(?:json)?\s*(.*)```$", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "findings" not in data:
        return None
    return data


def build_review_comments(findings, valid_lines_by_file):
    review_comments = []
    unanchored_notes = []

    for finding in findings:
        file_path = finding.get("file")
        line = finding.get("line")
        issue = finding.get("issue", "").strip()
        fix_prompt = finding.get("fix_prompt", "").strip()
        suggestion = finding.get("suggestion")
        severity = finding.get("severity", "medium").upper()

        valid_lines = valid_lines_by_file.get(file_path, [])
        if file_path and line in valid_lines and issue:
            body = f"**{severity}**: {issue}"
            if suggestion is not None:
                body += f"\n\n**Suggestion:**\n```suggestion\n{suggestion}\n```"
            if fix_prompt:
                # Self-contained so copying just this fenced block (without
                # the issue line above it) is still enough context for an
                # AI assistant to act on.
                copy_prompt = (
                    f"In {file_path} at line {line}: {issue}\n\nFix: {fix_prompt}"
                )
                body += f"\n\n**Copy-paste fix prompt:**\n```\n{copy_prompt}\n```"
            review_comments.append(
                {"path": file_path, "line": line, "side": "RIGHT", "body": body}
            )
        elif issue:
            unanchored_notes.append(f"- `{file_path}` (line {line}): {issue}")

    return review_comments, unanchored_notes


def post_fallback_comment(pull_request, body_text):
    final_comment = f"{COMMENT_MARKER}\n### \U0001F916 LangGraph AI Code Reviewer\n\n{body_text}"
    existing = None
    for comment in pull_request.get_issue_comments():
        if COMMENT_MARKER in comment.body:
            existing = comment
            break
    if existing:
        existing.edit(final_comment)
    else:
        pull_request.create_issue_comment(final_comment)


def post_to_github_node(state: ReviewState):
    print("Posting review back to GitHub Pull Request...")

    g = Github(os.environ["GITHUB_TOKEN"])
    repo = g.get_repo(os.environ["GITHUB_REPOSITORY"])

    with open(os.environ["GITHUB_EVENT_PATH"], "r") as f:
        event_data = json.load(f)
    pr_number = event_data["number"]

    pull_request = repo.get_pull(pr_number)

    if state.get("error") and not state.get("review_comments"):
        post_fallback_comment(
            pull_request,
            "The reviewer failed to produce a review after retrying.\n\n"
            f"Last error: `{state['error']}`",
        )
        return {}

    review_data = parse_review_json(state["review_comments"])
    if review_data is None:
        # Model didn't return valid JSON - fall back to posting whatever
        # text it did produce, so a review still shows up.
        post_fallback_comment(pull_request, state["review_comments"])
        return {}

    findings = review_data.get("findings", [])
    summary = review_data.get("summary", "").strip() or "LGTM"

    if not findings:
        post_fallback_comment(pull_request, summary)
        return {}

    review_comments, unanchored_notes = build_review_comments(
        findings, state.get("valid_lines", {})
    )

    body = summary
    if unanchored_notes:
        body += "\n\n**Additional notes (couldn't anchor to a line):**\n" + "\n".join(
            unanchored_notes
        )

    if not review_comments:
        post_fallback_comment(pull_request, body)
        return {}

    try:
        head_commit = repo.get_commit(pull_request.head.sha)
        pull_request.create_review(
            commit=head_commit,
            body=body,
            event="COMMENT",
            comments=review_comments,
        )
    except Exception as e:
        post_fallback_comment(
            pull_request,
            f"{body}\n\n(Inline review comments failed to post: `{e}`; showing summary only.)",
        )

    return {}


def sanity_check_edge(state: ReviewState):
    # If the API call failed, try one more time before giving up.
    if state["error"] and state["iterations"] < 2:
        return "retry"
    return "post_to_github"


def build_graph():
    workflow = StateGraph(ReviewState)
    workflow.add_node("analyzer", analyze_code_node)
    workflow.add_node("publisher", post_to_github_node)

    workflow.add_edge(START, "analyzer")
    workflow.add_conditional_edges(
        "analyzer",
        sanity_check_edge,
        {
            "retry": "analyzer",
            "post_to_github": "publisher",
        },
    )
    workflow.add_edge("publisher", END)
    return workflow.compile()


if __name__ == "__main__":
    g = Github(os.environ["GITHUB_TOKEN"])
    repo = g.get_repo(os.environ["GITHUB_REPOSITORY"])
    with open(os.environ["GITHUB_EVENT_PATH"], "r") as f:
        event_data = json.load(f)

    pr = repo.get_pull(event_data["number"])

    changes_payload, valid_lines_by_file = gather_changes(pr)

    if not changes_payload.strip():
        print("No supported code files were changed in this PR. Skipping.")
        sys.exit(0)

    agent = build_graph()
    initial_state = {
        "code_changes": changes_payload,
        "valid_lines": valid_lines_by_file,
        "review_comments": "",
        "iterations": 0,
        "error": "",
    }
    agent.invoke(initial_state)
