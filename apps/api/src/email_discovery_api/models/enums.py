"""String enum definitions for domain model status and role values."""

from enum import StrEnum


class OrganizationStatus(StrEnum):
    """Organization account lifecycle status."""

    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class UserStatus(StrEnum):
    """User account lifecycle status."""

    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DELETED = "DELETED"


class MembershipRole(StrEnum):
    """User authorization role within an Organization."""

    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"
    VIEWER = "VIEWER"


class MembershipStatus(StrEnum):
    """Organization membership status."""

    ACTIVE = "ACTIVE"
    INVITED = "INVITED"
    SUSPENDED = "SUSPENDED"


class ScanJobStatus(StrEnum):
    """Batch scan job status."""

    DRAFT = "DRAFT"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
    FAILED = "FAILED"


class ScanJobSourceType(StrEnum):
    """Input source type for a batch scan job."""

    MANUAL = "MANUAL"
    CSV = "CSV"
    XLSX = "XLSX"
    API = "API"


class ScanURLStatus(StrEnum):
    """Individual target URL processing status."""

    INVALID = "INVALID"
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    LEASED = "LEASED"
    SCANNING = "SCANNING"
    RETRY_WAIT = "RETRY_WAIT"
    COMPLETED = "COMPLETED"
    NO_EMAIL = "NO_EMAIL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    DUPLICATE = "DUPLICATE"


class RefreshSessionStatus(StrEnum):
    """Refresh session security lifecycle status."""

    ACTIVE = "ACTIVE"
    ROTATED = "ROTATED"
    REVOKED = "REVOKED"
    COMPROMISED = "COMPROMISED"


class CrawlAttemptOutcome(StrEnum):
    """Execution outcome of a single crawl attempt."""

    COMPLETED = "COMPLETED"
    COMPLETED_NO_EMAILS = "COMPLETED_NO_EMAILS"
    PARTIAL = "PARTIAL"
    ROBOTS_BLOCKED = "ROBOTS_BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class CrawledPageOutcome(StrEnum):
    """Processing outcome of an individual page within a crawl attempt."""

    FETCHED = "FETCHED"
    ROBOTS_BLOCKED = "ROBOTS_BLOCKED"
    FETCH_FAILED = "FETCH_FAILED"
    SKIPPED = "SKIPPED"
    PARSE_FAILED = "PARSE_FAILED"


class EmailClassification(StrEnum):
    """Classification category of a discovered email address."""

    ROLE_BASED = "ROLE_BASED"
    PERSONAL_OR_NAMED = "PERSONAL_OR_NAMED"
    NO_REPLY = "NO_REPLY"
    UNKNOWN = "UNKNOWN"


class EmailValidationStatus(StrEnum):
    """Validation state of a canonical email finding."""

    VALID = "VALID"
    UNVERIFIED = "UNVERIFIED"
    INVALID = "INVALID"


class EmailSourceType(StrEnum):
    """Source origin of an extracted email candidate."""

    VISIBLE_TEXT = "VISIBLE_TEXT"
    MAILTO = "MAILTO"
    OBFUSCATED_TEXT = "OBFUSCATED_TEXT"
