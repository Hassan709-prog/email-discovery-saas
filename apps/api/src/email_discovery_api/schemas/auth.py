"""Pydantic schemas for authentication and identity endpoints."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    """Registration request payload."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1)
    display_name: str | None = None
    organization_name: str = Field(min_length=1, max_length=255)
    organization_slug: str | None = None


class LoginRequest(BaseModel):
    """Login request payload."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1)
    organization_id: UUID | None = None


class OrganizationChoiceSchema(BaseModel):
    """Organization choice option for users with multiple active memberships."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    name: str
    slug: str
    role: str


class OrganizationSelectionRequiredResponse(BaseModel):
    """Response returned when login user has multiple organizations and none was selected."""

    model_config = ConfigDict(frozen=True)

    organization_selection_required: bool = True
    organizations: list[OrganizationChoiceSchema]


class AuthSuccessResponse(BaseModel):
    """Successful authentication payload containing access token details."""

    model_config = ConfigDict(frozen=True)

    access_token: str
    token_type: str = "Bearer"
    expires_in_seconds: int


class UserProfileResponse(BaseModel):
    """Safe authenticated user and tenant profile response."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    email: str
    display_name: str | None
    status: str
    organization_id: UUID
    organization_name: str
    organization_slug: str
    role: str
