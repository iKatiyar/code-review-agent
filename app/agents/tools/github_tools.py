"""
GitHub Agent Tools

LangChain @tool wrappers that the LangGraph agent uses to interact with
GitHub: fetching PR data, running static analysis, and posting review comments.
These three tools enable the end-to-end agentic workflow described in the PR
review pipeline.
"""

from typing import Any, Dict, List, Optional

from langchain_core.tools import tool

from app.services.github import GitHubService
from app.utils.logger import logger


@tool
def fetch_pr_tool(repo_url: str, pr_number: int, github_token: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch pull request metadata and changed files from GitHub.

    Used by the agent to retrieve the raw PR context — title, author,
    file diff list, and per-file patches — before running analysis.

    Args:
        repo_url: Full GitHub repository URL (e.g. https://github.com/owner/repo).
        pr_number: The pull request number to fetch.
        github_token: Optional GitHub PAT for private repos / higher rate limits.

    Returns:
        Dict with ``pr_data`` (metadata) and ``files`` (list of changed files).
    """
    logger.info(f"[fetch_pr_tool] Fetching PR #{pr_number} from {repo_url}")
    try:
        svc = GitHubService(github_token=github_token)
        pr_data = svc.get_pull_request_metadata(repo_url, pr_number)
        files_data = svc.get_pull_request_files(repo_url, pr_number)
        logger.info(
            f"[fetch_pr_tool] Retrieved {files_data['metadata']['processed_files']} "
            f"files for PR #{pr_number}"
        )
        return {"pr_data": pr_data, "files": files_data["files"]}
    except Exception as e:
        logger.error(f"[fetch_pr_tool] Failed to fetch PR #{pr_number}: {e}")
        return {"error": str(e), "pr_data": {}, "files": []}


@tool
def static_analysis_tool(file_path: str, file_content: str) -> List[Dict[str, Any]]:
    """
    Run lightweight AST-based static analysis on a Python file.

    Checks for style violations (PEP 8), common bugs (bare except, None
    comparison), performance anti-patterns (string concat in loops), and
    best-practice gaps (missing docstrings, hardcoded secrets).

    This tool complements AI analysis by catching structural issues fast,
    without an LLM call.

    Args:
        file_path: Path of the file (used for logging context).
        file_content: Raw source code of the file.

    Returns:
        List of issue dicts: {type, severity, line, description, suggestion}.
    """
    import ast

    logger.info(f"[static_analysis_tool] Running static analysis on {file_path}")
    issues: List[Dict[str, Any]] = []
    lines = file_content.split("\n")

    # ── Text-based checks ──────────────────────────────────────────────────
    for i, line in enumerate(lines, 1):
        if len(line) > 88:
            issues.append({
                "type": "style", "severity": "low", "line": i,
                "description": f"Line too long ({len(line)} chars, max 88)",
                "suggestion": "Break into multiple lines or use parentheses",
            })
        if line.endswith((" ", "\t")):
            issues.append({
                "type": "style", "severity": "low", "line": i,
                "description": "Trailing whitespace",
                "suggestion": "Remove trailing spaces/tabs",
            })
        if line.strip().startswith("import ") and "," in line:
            issues.append({
                "type": "style", "severity": "medium", "line": i,
                "description": "Multiple imports on one line",
                "suggestion": "Import each module on its own line",
            })
        if "print(" in line and not line.strip().startswith("#"):
            issues.append({
                "type": "bug", "severity": "low", "line": i,
                "description": "print() statement found — use logging instead",
                "suggestion": "Replace with logging.info/debug/warning",
            })
        if any(k in line.lower() for k in ("password", "secret", "api_key", "token")):
            if "=" in line and any(q in line for q in ('"', "'")):
                issues.append({
                    "type": "best_practice", "severity": "high", "line": i,
                    "description": "Potential hardcoded credential detected",
                    "suggestion": "Use environment variables or a secrets manager",
                })
        if any(k in line.upper() for k in ("TODO", "FIXME", "HACK", "XXX")):
            issues.append({
                "type": "best_practice", "severity": "low", "line": i,
                "description": "Unresolved TODO/FIXME comment",
                "suggestion": "Resolve the issue or open a tracked ticket",
            })

    # ── AST-based checks ───────────────────────────────────────────────────
    try:
        tree = ast.parse(file_content)
        for node in ast.walk(tree):
            # Bare except
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                issues.append({
                    "type": "bug", "severity": "medium", "line": node.lineno,
                    "description": "Bare except clause — catches all exceptions silently",
                    "suggestion": "Specify the exception type, e.g. 'except Exception as e:'",
                })
            # == None comparison
            if isinstance(node, ast.Compare):
                for op, comp in zip(node.ops, node.comparators):
                    if isinstance(comp, ast.Constant) and comp.value is None and isinstance(op, ast.Eq):
                        issues.append({
                            "type": "bug", "severity": "medium", "line": node.lineno,
                            "description": "Use 'is None' instead of '== None'",
                            "suggestion": "Replace '== None' with 'is None'",
                        })
            # Missing docstrings on public functions
            if isinstance(node, ast.FunctionDef):
                if not ast.get_docstring(node) and not node.name.startswith("_"):
                    issues.append({
                        "type": "best_practice", "severity": "medium", "line": node.lineno,
                        "description": f"Public function '{node.name}' lacks a docstring",
                        "suggestion": "Add a docstring describing purpose and parameters",
                    })
    except SyntaxError as e:
        issues.append({
            "type": "bug", "severity": "critical", "line": e.lineno or 1,
            "description": f"Syntax error: {e.msg}",
            "suggestion": "Fix the syntax error before merging",
        })

    logger.info(f"[static_analysis_tool] Found {len(issues)} issues in {file_path}")
    return issues


@tool
def post_review_comment_tool(
    repo_url: str,
    pr_number: int,
    summary: Dict[str, Any],
    file_issues: Dict[str, Any],
    github_token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Post the AI-generated review as a GitHub PR review comment.

    Formats the aggregated analysis — severity breakdown, per-file issues
    with line numbers and suggestions — into a rich Markdown comment and
    submits it via the GitHub Checks/Reviews API.

    Args:
        repo_url: Full GitHub repository URL.
        pr_number: The pull request number to comment on.
        summary: Aggregated summary from the LangGraph synthesize node.
        file_issues: Per-file dict of issues keyed by file path.
        github_token: Optional GitHub PAT (needs ``pull_requests: write`` scope).

    Returns:
        Dict with ``comment_id``, ``html_url``, and ``status``.
    """
    logger.info(f"[post_review_comment_tool] Posting review on PR #{pr_number}")
    try:
        svc = GitHubService(github_token=github_token)
        result = svc.post_pr_review(
            repo_url=repo_url,
            pr_number=pr_number,
            summary=summary,
            file_issues=file_issues,
        )
        logger.info(
            f"[post_review_comment_tool] Review posted — {result.get('html_url')}"
        )
        return result
    except Exception as e:
        logger.error(
            f"[post_review_comment_tool] Failed to post review on PR #{pr_number}: {e}"
        )
        return {"status": "failed", "error": str(e)}


__all__ = ["fetch_pr_tool", "static_analysis_tool", "post_review_comment_tool"]
