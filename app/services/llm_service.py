"""
LLM Service for AI-powered Code Analysis

Handles all interactions with Claude (Anthropic) for structured,
validated code review output via the instructor library.
"""

from typing import Any, Dict, List

import anthropic
import instructor
from pydantic import BaseModel, Field, field_validator

from app.config.settings import get_settings
from app.models.database import IssueType, IssueSeverity
from app.utils.logger import logger


# Pydantic models for structured output from Claude
class AIAnalysisIssue(BaseModel):
    """Validated issue structure from Claude analysis."""

    type: IssueType = Field(..., description="The type of the issue.")
    severity: IssueSeverity = Field(..., description="The severity of the issue.")
    line: int = Field(..., description="The line number where the issue occurs.")
    description: str = Field(..., description="A description of the issue.")
    suggestion: str = Field(..., description="A suggestion to fix the issue.")

    @field_validator("type", mode="before")
    def validate_issue_type(cls, v):
        try:
            return IssueType(v.lower())
        except ValueError:
            logger.warning(f"Invalid issue type '{v}', defaulting to 'best_practice'.")
            return IssueType.BEST_PRACTICE

    @field_validator("severity", mode="before")
    def validate_issue_severity(cls, v):
        try:
            return IssueSeverity(v.lower())
        except ValueError:
            logger.warning(f"Invalid issue severity '{v}', defaulting to 'low'.")
            return IssueSeverity.LOW


class AIAnalysisResult(BaseModel):
    """Structured analysis result from Claude."""

    issues: List[AIAnalysisIssue] = Field(
        ..., description="A list of issues found in the code."
    )


class LLMService:
    """
    Service for interacting with Claude (Anthropic) via instructor
    for structured, validated code review output.
    """

    def __init__(self):
        self.settings = get_settings()

        api_key = self.settings.llm.anthropic_api_key
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is not configured. "
                "Set it in your .env file or environment."
            )

        self.model = self.settings.llm.model or "claude-3-5-sonnet-20241022"
        logger.info(f"LLM service initialised — model: {self.model}")

        # instructor patches the Anthropic client to return Pydantic models directly
        self.client = instructor.from_anthropic(
            anthropic.AsyncAnthropic(api_key=api_key)
        )

    async def analyze_code(
        self, file_path: str, code_content: str, analysis_type: str
    ) -> List[Dict[str, Any]]:
        """
        Analyze code content using Claude and return structured issues.

        Args:
            file_path: Path of the file being reviewed.
            code_content: Source code to analyze.
            analysis_type: Focus area (e.g. 'comprehensive', 'bug', 'security').

        Returns:
            List of validated issue dicts ready for database persistence.
        """
        prompt = self._build_prompt(file_path, code_content, analysis_type)

        try:
            logger.debug(f"Sending {analysis_type} analysis request for {file_path}")

            response: AIAnalysisResult = await self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                response_model=AIAnalysisResult,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
            )

            issues = [issue.model_dump() for issue in response.issues]
            logger.info(f"Claude found {len(issues)} issues in {file_path}")
            return issues

        except Exception as e:
            logger.error(f"Claude API call failed for {file_path}: {e}")
            return []

    def _build_prompt(
        self, file_path: str, code_content: str, analysis_type: str
    ) -> str:
        valid_types = ", ".join([e.value for e in IssueType])
        valid_severities = ", ".join([e.value for e in IssueSeverity])

        return f"""You are an expert code reviewer performing a **{analysis_type.upper()}** analysis.

Review the following code from `{file_path}` and identify all issues.

```
{code_content}
```

For every issue found provide:
- `type`: one of [{valid_types}]
- `severity`: one of [{valid_severities}]
- `line`: the exact line number
- `description`: a clear explanation of the problem
- `suggestion`: a concrete fix or improvement

If no issues are found, return an empty issues list.
Focus exclusively on **{analysis_type}** concerns."""


__all__ = ["LLMService"]
