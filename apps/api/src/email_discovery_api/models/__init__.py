"""Application domain models package exporting all entities, enums, and helpers."""

from email_discovery_api.models.audit_log import AuditLog
from email_discovery_api.models.base import Base
from email_discovery_api.models.crawl_attempt import CrawlAttempt
from email_discovery_api.models.crawled_page import CrawledPage
from email_discovery_api.models.email_evidence import EmailEvidence
from email_discovery_api.models.email_finding import EmailFinding
from email_discovery_api.models.enums import (
    CrawlAttemptOutcome,
    CrawledPageOutcome,
    EmailClassification,
    EmailSourceType,
    EmailValidationStatus,
    MembershipRole,
    MembershipStatus,
    OrganizationStatus,
    RefreshSessionStatus,
    ScanJobSourceType,
    ScanJobStatus,
    ScanURLStatus,
    UserStatus,
)
from email_discovery_api.models.helpers import normalize_email, normalize_org_slug
from email_discovery_api.models.job_event import JobEvent
from email_discovery_api.models.membership import Membership
from email_discovery_api.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from email_discovery_api.models.organization import Organization
from email_discovery_api.models.refresh_session import RefreshSession
from email_discovery_api.models.rejected_email_candidate import RejectedEmailCandidate
from email_discovery_api.models.scan_job import ScanJob
from email_discovery_api.models.scan_url import ScanURL
from email_discovery_api.models.user import User

__all__ = [
    "AuditLog",
    "Base",
    "CrawlAttempt",
    "CrawlAttemptOutcome",
    "CrawledPage",
    "CrawledPageOutcome",
    "EmailClassification",
    "EmailEvidence",
    "EmailFinding",
    "EmailSourceType",
    "EmailValidationStatus",
    "JobEvent",
    "Membership",
    "MembershipRole",
    "MembershipStatus",
    "Organization",
    "OrganizationStatus",
    "RefreshSession",
    "RefreshSessionStatus",
    "RejectedEmailCandidate",
    "ScanJob",
    "ScanJobSourceType",
    "ScanJobStatus",
    "ScanURL",
    "ScanURLStatus",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "User",
    "UserStatus",
    "normalize_email",
    "normalize_org_slug",
]
