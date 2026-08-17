"""API response schemas for tenant-scoped email findings, detail, and evidence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RepresentativeEvidenceApiResponse(BaseModel):
    """Bounded, non-sensitive evidence preview snippet for a finding."""

    model_config = ConfigDict(frozen=True)

    evidence_id: UUID = Field(..., description="Unique evidence ID")
    source_type: str = Field(
        ..., description="Discovery source type (e.g. VISIBLE_TEXT, MAILTO_LINK)"
    )
    sanitized_page_url: str = Field(
        ..., description="Sanitized URL of page where evidence was discovered"
    )
    snippet: str | None = Field(None, description="Bounded text snippet context")
    created_at: datetime = Field(..., description="Evidence recording timestamp")


class ScanJobResultItemApiResponse(BaseModel):
    """Tenant-scoped canonical email finding summary schema for list view."""

    model_config = ConfigDict(frozen=True)

    finding_id: UUID = Field(..., description="Unique finding ID")
    canonical_email: str = Field(..., description="Discovered canonical email address")
    email_domain: str = Field(..., description="Domain portion of canonical email")
    classification: str = Field(..., description="Email category classification")
    is_role_based: bool = Field(..., description="Role-based address flag")
    validation_status: str = Field(..., description="Email validation status")
    evidence_count: int = Field(..., description="Total evidence count for this finding")
    first_found_at: datetime = Field(..., description="First discovery timestamp")
    last_found_at: datetime = Field(..., description="Latest discovery timestamp")
    representative_evidence: list[RepresentativeEvidenceApiResponse] = Field(
        default_factory=list[RepresentativeEvidenceApiResponse],
        description="Top representative evidence items (max 3)",
    )


class ScanJobResultDetailApiResponse(BaseModel):
    """Tenant-scoped finding detail schema with full metadata and evidence preview."""

    model_config = ConfigDict(frozen=True)

    finding_id: UUID = Field(..., description="Unique finding ID")
    job_id: UUID = Field(..., description="Associated scan job ID")
    canonical_email: str = Field(..., description="Discovered canonical email address")
    email_domain: str = Field(..., description="Domain portion of canonical email")
    classification: str = Field(..., description="Email category classification")
    is_role_based: bool = Field(..., description="Role-based address flag")
    validation_status: str = Field(..., description="Email validation status")
    evidence_count: int = Field(..., description="Total evidence count for this finding")
    first_found_at: datetime = Field(..., description="First discovery timestamp")
    last_found_at: datetime = Field(..., description="Latest discovery timestamp")
    created_at: datetime = Field(..., description="Record creation timestamp")
    updated_at: datetime = Field(..., description="Record update timestamp")
    representative_evidence: list[RepresentativeEvidenceApiResponse] = Field(
        default_factory=list[RepresentativeEvidenceApiResponse],
        description="Top representative evidence items (max 3)",
    )


class FindingEvidenceItemApiResponse(BaseModel):
    """Paginated evidence detail schema for a specific email finding."""

    model_config = ConfigDict(frozen=True)

    evidence_id: UUID = Field(..., description="Unique evidence ID")
    source_type: str = Field(..., description="Discovery source type")
    sanitized_page_url: str = Field(..., description="Sanitized page URL")
    snippet: str | None = Field(None, description="Bounded text snippet context")
    confidence: float = Field(..., description="Evidence confidence score")
    crawled_page_status_code: int | None = Field(None, description="Crawled page HTTP status code")
    crawled_page_depth: int | None = Field(None, description="Crawled page crawl depth")
    created_at: datetime = Field(..., description="Evidence creation timestamp")
