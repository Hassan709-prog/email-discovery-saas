/**
 * Exact API type definitions matching FastAPI backend schemas.
 */

export interface OrganizationChoiceSchema {
  id: string;
  name: string;
  slug: string;
  role: string;
}

export interface AuthSuccessResponse {
  access_token: string;
  token_type: string;
  expires_in_seconds: number;
}

export interface OrganizationSelectionRequiredResponse {
  organization_selection_required: true;
  organizations: OrganizationChoiceSchema[];
}

export interface UserProfileResponse {
  id: string;
  email: string;
  display_name: string | null;
  status: string;
  organization_id: string;
  organization_name: string;
  organization_slug: string;
  role: string;
}

export interface UserSchema {
  id: string;
  email: string;
  display_name: string | null;
  status: string;
}

export interface OrganizationSchema {
  id: string;
  name: string;
  slug: string;
  role: string;
}

export interface RegisterRequest {
  email: string;
  password: string;
  display_name?: string | null;
  organization_name: string;
}

export interface LoginRequest {
  email: string;
  password: string;
  organization_id?: string | null;
}

export interface ApiErrorDetail {
  code: string;
  message: string;
  details?: unknown;
  request_id?: string;
}

export interface ApiErrorEnvelope {
  error: ApiErrorDetail;
}

export class ApiError extends Error {
  code: string;
  details?: unknown;
  requestId?: string;
  status: number;

  constructor(status: number, detail: ApiErrorDetail) {
    super(detail.message || `API error (${status})`);
    this.name = 'ApiError';
    this.status = status;
    this.code = detail.code || 'UNKNOWN_ERROR';
    this.details = detail.details;
    this.requestId = detail.request_id;
  }
}
