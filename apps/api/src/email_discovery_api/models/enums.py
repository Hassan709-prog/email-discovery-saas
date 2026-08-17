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
