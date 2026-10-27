"""Tool schemas the models must answer with, and the pydantic models their answers are validated against.

Structured output is a forced tool call (Converse ``toolChoice {"tool": ...}``, Anthropic ``tool_choice``), so
an answer either fits the schema or is repaired once and then discarded.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mesh.llm.base import ToolSpec

RECORD_QUERY = ToolSpec(
    name="record_query",
    description="Record the SQL that answers the question, or say why it cannot be answered from the datasets shown.",
    input_schema={
        "type": "object",
        "properties": {
            "answerable": {"type": "boolean"},
            "sql": {"type": "string", "description": "One Athena (Trino) SELECT over the tables shown, filtered on dt."},
            "tables": {"type": "array", "items": {"type": "string"}},
            "explanation": {"type": "string", "maxLength": 600},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "missing": {"type": "string", "description": "When not answerable: what data would be needed."},
        },
        "required": ["answerable", "explanation"],
    },
)

RECORD_DIAGNOSIS = ToolSpec(
    name="record_diagnosis",
    description="Record the most likely cause of the failed run, citing evidence ids, and the next steps.",
    input_schema={
        "type": "object",
        "properties": {
            "category": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "string"}, "description": "Evidence ids, e.g. E3"},
            "summary": {"type": "string", "maxLength": 500},
            "next_steps": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        },
        "required": ["category", "evidence", "summary", "next_steps"],
    },
)

RECORD_REVIEW = ToolSpec(
    name="record_review",
    description="Record the review comment for the change, built only from the findings given.",
    input_schema={
        "type": "object",
        "properties": {
            "summary": {"type": "string", "maxLength": 600},
            "finding_notes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"finding": {"type": "string"}, "note": {"type": "string", "maxLength": 400}},
                    "required": ["finding", "note"],
                },
            },
            "suggested_path": {"type": "string", "maxLength": 600},
        },
        "required": ["summary", "finding_notes", "suggested_path"],
    },
)

TOOLS_FOR_PROMPT: dict[str, list[ToolSpec]] = {
    "ask_sql": [RECORD_QUERY],
    "triage": [RECORD_DIAGNOSIS],
    "review": [RECORD_REVIEW],
}


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class QueryDraft(_M):
    answerable: bool
    sql: str = ""
    tables: list[str] = Field(default_factory=list)
    explanation: str = ""
    assumptions: list[str] = Field(default_factory=list)
    missing: str = ""


class DiagnosisDraft(_M):
    category: str
    evidence: list[str]
    summary: str = Field(max_length=1000)
    next_steps: list[str] = Field(default_factory=list, max_length=8)


class FindingNote(_M):
    finding: str
    note: str


class ReviewDraft(_M):
    summary: str
    finding_notes: list[FindingNote] = Field(default_factory=list)
    suggested_path: str = ""


Verdict = Literal["approve", "needs_approval", "block"]
