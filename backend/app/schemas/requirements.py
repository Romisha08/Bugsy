from __future__ import annotations

from pydantic import BaseModel, Field


class ParseTextRequest(BaseModel):
    text: str
    python_version: str = Field(min_length=3, max_length=16)


class ParsedRequirement(BaseModel):
    line: int
    original: str
    name: str
    specifier: str
    extras: list[str]
    marker: str | None


class ParseResponse(BaseModel):
    source_id: str
    parse_status: str = "parsed"
    python_version: str
    requirements: list[ParsedRequirement]
    warnings: list[str] = Field(default_factory=list)
    source_sha256: str


class ParseIssue(BaseModel):
    line: int
    original: str
    reason: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ParseIssue] | dict[str, str] | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody
