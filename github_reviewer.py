import os
import sys
import json

from github import Github
from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict

# Marker used to find and update our own previous comment instead of
# spamming a new one on every push to the PR.
COMMENT_MARKER = "<!-- ai-code-reviewer:groq-langgraph -->"

# Rough char budget so the diff payload stays inside the model's context
# window alongside the system/instruction prompt and the model's reply.
MAX_PAYLOAD_CHARS = 40000

REVIEWED_EXTENSIONS = (".py", ".js", ".ts", ".go")


class ReviewState(TypedDict):
    code_changes: str
    review_comments: str
    iterations: int
    error: str


def build_llm():
    model_name = os.environ.get("GROQ_MODEL") or "llama-3.1-8b-instant"
    return ChatGroq(
        model=model_name,
        temperature=0.0,
        groq_api_key=os.environ["GROQ_API_KEY"],
    )


def analyze_code_node(state: ReviewState):
    current_iter = state.get("iterations", 0)
    print(f"Running Analysis Loop - Iteration {current_iter + 1}")

    prompt = f"""You are an elite senior software engineer auditing a GitHub Pull Request.
Review the following code changes for security flaws, performance bugs, or bad practices.

Code to Review:
{state['code_changes']}

Provide your output in clean Markdown with clear headings. If the code looks perfect, say 'LGTM' (Looks Good To Me).
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


def post_to_github_node(state: ReviewState):
    print("Posting review back to GitHub Pull Request...")

    g = Github(os.environ["GITHUB_TOKEN"])
    repo = g.get_repo(os.environ["GITHUB_REPOSITORY"])

    with open(os.environ["GITHUB_EVENT_PATH"], "r") as f:
        event_data = json.load(f)
    pr_number = event_data["number"]

    pull_request = repo.get_pull(pr_number)

    if state.get("error") and not state.get("review_comments"):
        body_text = (
            "The reviewer failed to produce a review after retrying.\n\n"
            f"Last error: `{state['error']}`"
        )
    else:
        body_text = state["review_comments"]

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


def gather_changes(pr):
    changes_payload = ""
    for file in pr.get_files():
        if not file.filename.endswith(REVIEWED_EXTENSIONS):
            continue
        if not file.patch:
            # Binary files or diffs GitHub considers too large omit `patch`.
            changes_payload += f"\nFile: {file.filename}\n(diff omitted by GitHub - binary or too large)\n"
            continue
        changes_payload += f"\nFile: {file.filename}\n{file.patch}\n"

    if len(changes_payload) > MAX_PAYLOAD_CHARS:
        changes_payload = (
            changes_payload[:MAX_PAYLOAD_CHARS]
            + "\n\n[... diff truncated to fit model context window ...]"
        )

    return changes_payload


if __name__ == "__main__":
    g = Github(os.environ["GITHUB_TOKEN"])
    repo = g.get_repo(os.environ["GITHUB_REPOSITORY"])
    with open(os.environ["GITHUB_EVENT_PATH"], "r") as f:
        event_data = json.load(f)

    pr = repo.get_pull(event_data["number"])

    changes_payload = gather_changes(pr)

    if not changes_payload.strip():
        print("No supported code files were changed in this PR. Skipping.")
        sys.exit(0)

    agent = build_graph()
    initial_state = {
        "code_changes": changes_payload,
        "review_comments": "",
        "iterations": 0,
        "error": "",
    }
    agent.invoke(initial_state)
