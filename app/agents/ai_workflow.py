"""
Intelligent AI-Driven Code Analysis Workflow

Defines a stateful LangGraph workflow where the agent:
  1. Triages the PR to identify files worth reviewing
  2. Loops over each file — calling Claude via the analyze_code_with_ai tool
  3. Synthesizes a severity-bucketed summary across all findings
  4. Posts the review back to GitHub as a PR comment (comment poster tool)
"""

from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, StateGraph

from app.agents.tools.ai_tools import analyze_code_with_ai
from app.agents.tools.github_tools import post_review_comment_tool
from app.services.llm_service import LLMService
from app.utils.logger import logger


class FileAnalysis(TypedDict):
    """Analysis results for a single file."""

    file_path: str
    issues: List[Dict[str, Any]]


class AIAnalysisState(TypedDict):
    """
    Full state passed between LangGraph nodes.

    Keeping everything in one TypedDict makes the workflow inspectable and
    resumable — each node receives the complete state and returns it updated.
    """

    pr_data: Dict[str, Any]
    files_data: List[Dict[str, Any]]
    critical_files: List[str]
    current_file_path: Optional[str]
    analysis_results: List[FileAnalysis]
    final_summary: Dict[str, Any]
    review_posted: bool
    llm_service: LLMService


class AIWorkflow:
    """
    Orchestrates the autonomous PR review agent via a LangGraph StateGraph.

    Graph topology:
        triage_pr
            │
            ▼
        file_analysis_loop ◄──────────┐
            │                         │
            ├── "continue" ───────────┘
            └── "end"
                    │
                    ▼
            synthesize_report
                    │
                    ▼
            post_review          (comment poster)
                    │
                    ▼
                  END
    """

    def __init__(self):
        self.graph = self._build_graph()
        logger.info("AI Agent analysis workflow initialised")

    def _build_graph(self) -> StateGraph:
        workflow = StateGraph(AIAnalysisState)

        workflow.add_node("triage_pr", self.triage_pr_node)
        workflow.add_node("file_analysis_loop", self.file_analysis_loop_node)
        workflow.add_node("synthesize_report", self.synthesize_report_node)
        workflow.add_node("post_review", self.post_review_node)

        workflow.set_entry_point("triage_pr")
        workflow.add_edge("triage_pr", "file_analysis_loop")
        workflow.add_conditional_edges(
            "file_analysis_loop",
            self.should_continue_analysis,
            {
                "continue": "file_analysis_loop",
                "end": "synthesize_report",
            },
        )
        workflow.add_edge("synthesize_report", "post_review")
        workflow.add_edge("post_review", END)

        return workflow.compile()

    async def run(
        self,
        pr_data: Dict[str, Any],
        files_data: List[Dict[str, Any]],
        github_token: Optional[str] = None,
        repo_url: Optional[str] = None,
        pr_number: Optional[int] = None,
        post_comment: bool = False,
    ) -> Dict[str, Any]:
        """
        Execute the full review workflow for a pull request.

        Args:
            pr_data: PR metadata dict (title, author, stats …).
            files_data: Changed files with content from GitHubService.
            github_token: Token for posting the review comment (optional).
            repo_url: Repo URL — required when post_comment=True.
            pr_number: PR number — required when post_comment=True.
            post_comment: Whether to call the comment-poster node at the end.
        """
        llm_service = LLMService()

        initial_state: AIAnalysisState = {
            "pr_data": pr_data,
            "files_data": files_data,
            "critical_files": [],
            "current_file_path": None,
            "analysis_results": [],
            "final_summary": {},
            "review_posted": False,
            "llm_service": llm_service,
            # Stash routing context so post_review_node can access it
            "_github_token": github_token,
            "_repo_url": repo_url,
            "_pr_number": pr_number,
            "_post_comment": post_comment,
        }

        final_state = await self.graph.ainvoke(initial_state)
        return self._format_output(final_state)

    # ──────────────────────────────────────────────────────────────────────
    # Nodes
    # ──────────────────────────────────────────────────────────────────────

    async def triage_pr_node(self, state: AIAnalysisState) -> AIAnalysisState:
        """
        Triage: decide which files deserve deep AI analysis.

        Current strategy — all Python files. This is the natural extension
        point for priority scoring (e.g. files with most additions, touching
        security-sensitive paths, etc.).
        """
        state["critical_files"] = [
            f["filename"]
            for f in state["files_data"]
            if f.get("filename", "").endswith(".py")
        ]
        logger.info(
            f"Triage complete — {len(state['critical_files'])} Python file(s) queued for analysis."
        )
        return state

    async def file_analysis_loop_node(self, state: AIAnalysisState) -> AIAnalysisState:
        """
        Process one file per invocation; the conditional edge loops back here
        until ``critical_files`` is empty.
        """
        if not state["critical_files"]:
            return state

        file_path = state["critical_files"].pop(0)
        state["current_file_path"] = file_path

        file_data = next(
            (f for f in state["files_data"] if f.get("filename") == file_path), None
        )
        if not file_data or not file_data.get("content"):
            logger.warning(f"No content for {file_path} — skipping.")
            return state

        logger.info(f"Analysing {file_path} with Claude …")
        issues = await analyze_code_with_ai(
            state["llm_service"], file_path, file_data["content"]
        )

        state["analysis_results"].append({"file_path": file_path, "issues": issues})
        return state

    def should_continue_analysis(self, state: AIAnalysisState) -> str:
        return "continue" if state["critical_files"] else "end"

    async def synthesize_report_node(self, state: AIAnalysisState) -> AIAnalysisState:
        """
        Aggregate per-file findings into a severity/type breakdown summary.
        """
        logger.info("Synthesising final report …")
        results = state["analysis_results"]
        total_issues = sum(len(r.get("issues", [])) for r in results)

        severity_breakdown: Dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        type_breakdown: Dict[str, int] = {}

        for result in results:
            for issue in result.get("issues", []):
                sev = issue.get("severity", "low")
                itype = issue.get("type", "style")
                if sev in severity_breakdown:
                    severity_breakdown[sev] += 1
                type_breakdown[itype] = type_breakdown.get(itype, 0) + 1

        state["final_summary"] = {
            "total_files_analyzed": len(results),
            "total_issues": total_issues,
            "severity_breakdown": severity_breakdown,
            "issue_type_breakdown": type_breakdown,
            "overall_summary": (
                f"AI analysis complete. "
                f"Found {total_issues} issue(s) across {len(results)} file(s)."
            ),
        }
        logger.info("Report synthesised.")
        return state

    async def post_review_node(self, state: AIAnalysisState) -> AIAnalysisState:
        """
        Comment-poster node: calls ``post_review_comment_tool`` to publish
        the aggregated review back to the GitHub PR.

        Skipped gracefully when ``post_comment=False`` or when repo/PR
        context is not available (e.g. during offline analysis).
        """
        post_comment = state.get("_post_comment", False)
        repo_url = state.get("_repo_url")
        pr_number = state.get("_pr_number")
        github_token = state.get("_github_token")

        if not post_comment or not repo_url or not pr_number:
            logger.info("Comment-poster skipped (post_comment=False or missing repo context).")
            return state

        logger.info(f"Posting review comment to PR #{pr_number} …")

        formatted_files = {}
        for fa in state.get("analysis_results", []):
            formatted_files[fa["file_path"]] = {
                "language": "python",
                "issues": fa["issues"],
            }

        result = post_review_comment_tool.invoke({
            "repo_url": repo_url,
            "pr_number": pr_number,
            "summary": state["final_summary"],
            "file_issues": formatted_files,
            "github_token": github_token,
        })

        state["review_posted"] = result.get("status") == "posted"
        if state["review_posted"]:
            logger.info(f"Review posted — {result.get('html_url')}")
        else:
            logger.warning(f"Review post failed: {result.get('error')}")

        return state

    # ──────────────────────────────────────────────────────────────────────
    # Output formatter
    # ──────────────────────────────────────────────────────────────────────

    def _format_output(self, final_state: AIAnalysisState) -> Dict[str, Any]:
        formatted_files = {}
        for fa in final_state.get("analysis_results", []):
            formatted_files[fa["file_path"]] = {
                "language": "python",
                "issues": fa["issues"],
            }

        return {
            "summary": final_state.get("final_summary", {}),
            "files": formatted_files,
            "review_posted": final_state.get("review_posted", False),
        }
