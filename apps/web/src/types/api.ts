/**
 * Exact API type definitions matching FastAPI backend schemas under /api/v1/scan-jobs.
 */

export type ScanJobStatus =
  | 'DRAFT'
  | 'QUEUED'
  | 'RUNNING'
  | 'CANCELLING'
  | 'CANCELLED'
  | 'COMPLETED'
  | 'COMPLETED_WITH_ERRORS'
  | 'FAILED';

export type ScanJobSourceType = 'MANUAL' | 'CSV' | 'XLSX' | 'API';

export type ScanURLStatus =
  | 'INVALID'
  | 'PENDING'
  | 'QUEUED'
  | 'LEASED'
  | 'SCANNING'
  | 'RETRY_WAIT'
  | 'COMPLETED'
  | 'NO_EMAIL'
  | 'FAILED'
  | 'CANCELLED'
  | 'DUPLICATE';

export type EmailClassification =
  | 'ROLE_BASED'
  | 'PERSONAL_OR_NAMED'
  | 'NO_REPLY'
  | 'UNKNOWN';

export type EmailValidationStatus = 'VALID' | 'UNVERIFIED' | 'INVALID';

export type EmailSourceType =
  | 'VISIBLE_TEXT'
  | 'MAILTO'
  | 'OBFUSCATED_TEXT';

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
  organization_name?: string | null;
  organization_slug?: string | null;
}

export interface LoginRequest {
  email: string;
  password: string;
  organization_id?: string | null;
}

export interface ScanJobApiResponse {
  id: string;
  organization_id: string;
  created_by_user_id: string;
  name: string | null;
  status: ScanJobStatus;
  source_type: ScanJobSourceType;
  scanner_version: string;
  normalization_version: string;
  ranking_version: string;
  configuration_snapshot: Record<string, unknown>;
  total_input_count: number;
  valid_input_count: number;
  duplicate_input_count: number;
  invalid_input_count: number;
  queued_count: number;
  running_count: number;
  completed_count: number;
  failed_count: number;
  email_finding_count: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  cancellation_requested_at: string | null;
}

export interface ScanJobProgressApiResponse {
  job_id: string;
  status: ScanJobStatus;
  progress_percentage: number;
  total_input_count: number;
  valid_input_count: number;
  duplicate_input_count: number;
  invalid_input_count: number;
  queued_count: number;
  running_count: number;
  completed_count: number;
  failed_count: number;
  email_finding_count: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface ScanURLDiagnosticsApiResponse {
  total_duration_seconds: number | null;
  pages_attempted: number | null;
  pages_fetched: number | null;
  retry_count: number | null;
  last_failure_code: string | null;
  selected_primary_email: string | null;
  primary_email_selection_version: string | null;
  plain_language_outcome: string | null;
  failure_reason: string | null;
}

export interface ScanURLApiResponse {
  id: string;
  scan_job_id: string;
  original_index: number;
  original_input: string;
  normalized_url: string | null;
  normalized_domain: string | null;
  status: ScanURLStatus;
  duplicate_of_scan_url_id: string | null;
  last_error_code: string | null;
  created_at: string;
  processing_duration_seconds?: number | null;
  retry_count?: number | null;
  pages_checked?: number | null;
  selected_primary_email?: string | null;
  primary_email_selection_version?: string | null;
  plain_language_outcome?: string | null;
  failure_reason?: string | null;
  diagnostics?: ScanURLDiagnosticsApiResponse | null;
}

export interface RepresentativeEvidenceApiResponse {
  evidence_id: string;
  source_type: EmailSourceType | string;
  sanitized_page_url: string;
  snippet: string | null;
  created_at: string;
}

export interface ScanJobResultItemApiResponse {
  finding_id: string;
  canonical_email: string;
  email_domain: string;
  classification: EmailClassification | string;
  is_role_based: boolean;
  validation_status: EmailValidationStatus | string;
  evidence_count: number;
  first_found_at: string;
  last_found_at: string;
  representative_evidence: RepresentativeEvidenceApiResponse[];
}

export interface ScanJobResultDetailApiResponse {
  finding_id: string;
  job_id: string;
  canonical_email: string;
  email_domain: string;
  classification: EmailClassification | string;
  is_role_based: boolean;
  validation_status: EmailValidationStatus | string;
  evidence_count: number;
  first_found_at: string;
  last_found_at: string;
  created_at: string;
  updated_at: string;
  representative_evidence: RepresentativeEvidenceApiResponse[];
}

export interface FindingEvidenceItemApiResponse {
  evidence_id: string;
  source_type: EmailSourceType | string;
  sanitized_page_url: string;
  snippet: string | null;
  confidence: number;
  crawled_page_status_code: number | null;
  crawled_page_depth: number | null;
  created_at: string;
}

export interface JobEventApiResponse {
  id: string;
  scan_job_id: string;
  event_type: string;
  sequence_number: number;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  next_cursor: string | null;
}

export interface PreviewScanInputsApiRequest {
  inputs: string[];
  configuration_snapshot?: Record<string, unknown>;
}

export interface ScanInputPreviewItemApiResponse {
  original_index: number;
  original_input: string;
  normalized_url: string | null;
  normalized_domain: string | null;
  classification: 'VALID' | 'DUPLICATE' | 'INVALID' | string;
  duplicate_of_index: number | null;
  error_code: string | null;
  error_message: string | null;
}

export interface PreviewScanInputsApiResponse {
  previews: ScanInputPreviewItemApiResponse[];
  total_input_count: number;
  valid_input_count: number;
  duplicate_input_count: number;
  invalid_input_count: number;
}

export interface CreateScanJobApiRequest {
  name?: string | null;
  source_type?: ScanJobSourceType;
  inputs: string[];
  configuration_snapshot?: Record<string, unknown>;
  scanner_version?: string;
  normalization_version?: string;
  ranking_version?: string;
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

export type AnalyticsPeriodEnum = '7d' | '30d' | '90d' | 'all';

export interface AnalyticsTimelinePoint {
  date: string;
  scans_created: number;
  emails_found: number;
}

export interface RecentScanJobSummary {
  id: string;
  name: string | null;
  status: ScanJobStatus;
  completed_at: string;
  valid_input_count: number;
  completed_count: number;
  failed_count: number;
  email_finding_count: number;
}

export interface AnalyticsOverviewResponse {
  period: AnalyticsPeriodEnum;
  start_at: string | null;
  end_at: string;
  total_scans: number;
  active_scans: number;
  websites_submitted: number;
  websites_processed: number;
  websites_completed: number;
  websites_failed: number;
  emails_discovered: number;
  successful_processing_rate: number;
  status_distribution: Record<string, number>;
  findings_by_classification: Record<string, number>;
  findings_by_validation_status: Record<string, number>;
  scan_activity_timeline: AnalyticsTimelinePoint[];
  recent_completed_scans: RecentScanJobSummary[];
}
