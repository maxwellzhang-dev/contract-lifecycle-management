export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "/backend-api";

export const contractStatuses = ["Quoting", "Under Review", "Cleared to Start", "In Progress", "Completed", "On Hold", "Cancelled"] as const;
export const approverRoles = ["legal", "finance", "procurement", "admin"] as const;
export const roles = ["admin", "legal", "procurement", "finance", "viewer"] as const;
export const obligationKinds = ["Deliverable", "Payment", "Milestone", "Compliance", "Renewal", "Other"] as const;
export const obligationStatuses = ["Open", "Done", "Waived"] as const;

export type Permission =
  | "contract.create" | "contract.edit" | "contract.delete" | "contract.validate"
  | "approval.submit" | "obligation.manage" | "audit.read" | "user.manage" | "reminders.run" | "template.manage" | "ai.use";

export type User = {
  id: number;
  email: string;
  name: string;
  role: string;
  active: boolean;
  permissions: Permission[];
};

export type Contract = {
  id: number;
  title: string;
  reference_number?: string | null;
  vendor_name?: string | null;
  project_name?: string | null;
  phase?: string | null;
  status: string;
  project_start_date?: string | null;
  project_end_date?: string | null;
  renewal_date?: string | null;
  currency?: string | null;
  one_time_amount?: number | null;
  optional_amount?: number | null;
  vendor_contact?: string | null;
  internal_contact?: string | null;
  deliverables?: string | null;
  payment_terms?: string | null;
  notes?: string | null;
  department?: string | null;
  owner?: string | null;
  validation_status: string;
  reviewed_by?: string | null;
  validated_at?: string | null;
  extraction_confidence?: number | null;
  missing_fields: string[];
  low_confidence_fields: string[];
  field_confidences: Record<string, number>;
  extraction_note?: string | null;
  file_name?: string | null;
  file_type?: string | null;
  upload_date?: string | null;
  uploaded_by?: string | null;
  document_version: number;
  tags: string[];
  pending_approval_id: number | null;
  allowed_transitions: string[];
};

export type Dashboard = {
  total: number;
  active: number;
  under_review: number;
  completed: number;
  draft: number;
  expired: number;
  awaiting_validation: number;
  missing_information: number;
  expiring_30_days: number;
  pending_approvals: number;
  obligations_overdue: number;
  obligations_due_30_days: number;
  value_by_currency: { currency: string; value: number }[];
  total_value_by_currency: { currency: string; value: number }[];
  status_distribution: { status: string; count: number }[];
};

export type AuditItem = {
  id: number;
  action: string;
  actor: string;
  details?: string;
  created_at: string;
  contract_title?: string;
  vendor_name?: string;
};

export type ApprovalStep = {
  id: number;
  step_order: number;
  role: string;
  status: "Pending" | "Waiting" | "Approved" | "Rejected" | "Skipped";
  decided_by?: number | null;
  decided_by_name?: string | null;
  decided_at?: string | null;
  comment?: string | null;
};

export type ApprovalRequest = {
  id: number;
  contract_id: number;
  status: "Pending" | "Approved" | "Rejected" | "Withdrawn" | "Cancelled";
  submitted_by: number;
  submitted_by_name: string;
  note?: string | null;
  submitted_at: string;
  completed_at?: string | null;
  steps: ApprovalStep[];
};

export type InboxItem = {
  step_id: number;
  role: string;
  step_order: number;
  total_steps: number;
  request_id: number;
  note?: string | null;
  submitted_at: string;
  submitted_by_name: string;
  contract_id: number;
  title: string;
  vendor_name?: string | null;
  currency?: string | null;
  one_time_amount?: number | null;
  optional_amount?: number | null;
  project_start_date?: string | null;
  project_end_date?: string | null;
};

export type Version = {
  id: number;
  version: number;
  file_name?: string | null;
  file_type?: string | null;
  file_size?: number | null;
  uploaded_by?: string | null;
  uploaded_at: string;
  note?: string | null;
};

export type Obligation = {
  id: number;
  contract_id: number;
  contract_title: string;
  contract_status: string;
  vendor_name?: string | null;
  title: string;
  kind: string;
  due_date?: string | null;
  amount?: number | null;
  currency?: string | null;
  owner?: string | null;
  status: string;
  notes?: string | null;
  completed_at?: string | null;
  completed_by?: string | null;
  days_until_due: number | null;
  overdue: boolean;
};

export type Notification = {
  id: number;
  kind: string;
  title: string;
  body?: string | null;
  contract_id?: number | null;
  created_at: string;
  read_at?: string | null;
};

export type Comment = {
  id: number;
  contract_id: number;
  user_id: number;
  author_name: string;
  author_role: string;
  body: string;
  created_at: string;
};

export type CurrencyValue = { currency: string; value: number };

export type VendorSummary = {
  vendor_name: string;
  contract_count: number;
  active_count: number;
  statuses: Record<string, number>;
  value_by_currency: CurrencyValue[];
  departments: string[];
  next_end_date: string | null;
  open_obligations: number;
  overdue_obligations: number;
  last_updated: string;
};

export type Report = {
  group_by: string;
  contract_count: number;
  value_by_currency: CurrencyValue[];
  rows: { group: string; count: number; active: number; completed: number; value_by_currency: CurrencyValue[] }[];
};

export type Template = {
  id: number;
  name: string;
  description?: string | null;
  body: string;
  defaults: Record<string, string>;
  active: boolean;
  placeholders: string[];
  created_by?: string | null;
  updated_at: string;
};

export type AiReviewResult = {
  summary: string;
  key_terms: { label: string; value: string; quote: string | null }[];
  risks: { category: string; severity: "high" | "medium" | "low"; finding: string; quote: string | null; recommendation: string }[];
  obligations: { title: string; kind: string; due_date: string | null; amount: number | null; currency: string | null; quote: string | null }[];
  missing_clauses: string[];
  field_mismatches: { field: string; record_value: string | null; document_value: string; quote: string | null }[];
};

export type AiReview = {
  id: number;
  document_version: number;
  model: string;
  created_by: string;
  created_at: string;
  stale: boolean;
  result: AiReviewResult;
};

export type AiAnswer = { answer: string; found: boolean; citations: { quote: string; note: string | null }[] };
export type AiQuestion = { id: number; question: string; answer: AiAnswer; asked_by: string; created_at: string; document_version: number | null };
export type AiComparison = {
  from_version: number;
  to_version: number;
  summary: string;
  changes: { topic: string; before: string | null; after: string | null; impact: "favourable" | "unfavourable" | "neutral" | "unclear"; note: string }[];
};
export type AiFilters = {
  search: string | null; status: string | null; vendor: string | null; department: string | null; owner: string | null;
  tag: string | null; currency: string | null; min_value: number | null; max_value: number | null;
  end_from: string | null; end_to: string | null; explanation: string;
};

export type DirectoryEntry = { id: number; name: string; role: string };

/** Thrown for 401 responses so the app can return to the sign-in screen. */
export class AuthError extends Error {}

async function read<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "Request failed" }));
    const detail = typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`;
    if (response.status === 401) throw new AuthError(detail);
    throw new Error(detail);
  }
  return response.json();
}

function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  return fetch(`${API_BASE}${path}`, { credentials: "include", ...init }).then(read<T>);
}

function json<T>(path: string, method: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export const fileUrl = (contractId: number, version?: number) =>
  version ? `${API_BASE}/api/contracts/${contractId}/versions/${version}/file` : `${API_BASE}/api/contracts/${contractId}/file`;
export const previewUrl = (contractId: number, version?: number) =>
  version ? `${API_BASE}/api/contracts/${contractId}/versions/${version}/preview` : `${API_BASE}/api/contracts/${contractId}/preview`;

export const api = {
  login: (email: string, password: string) => json<{ user: User }>("/api/auth/login", "POST", { email, password }),
  logout: () => json<{ signed_out: boolean }>("/api/auth/logout", "POST"),
  me: () => request<User>("/api/auth/me"),
  changePassword: (current_password: string, new_password: string) =>
    json<{ changed: boolean }>("/api/auth/password", "POST", { current_password, new_password }),

  dashboard: (months = 12) => request<Dashboard>(`/api/dashboard?months=${months}`),
  contracts: (query = "") => request<Contract[]>(`/api/contracts${query}`),
  contract: (id: number) => request<Contract>(`/api/contracts/${id}`),
  audit: (contractId?: number) => request<AuditItem[]>(`/api/audit${contractId ? `?contract_id=${contractId}` : ""}`),
  update: (id: number, values: Partial<Contract>) => json<Contract>(`/api/contracts/${id}`, "PATCH", values),
  validate: (id: number) => json<Contract>(`/api/contracts/${id}/validate`, "POST"),
  remove: (id: number) => json<{ deleted: boolean; id: number }>(`/api/contracts/${id}`, "DELETE"),
  upload: (form: FormData) => request<Contract>("/api/contracts/upload", { method: "POST", body: form }),

  versions: (id: number) => request<Version[]>(`/api/contracts/${id}/versions`),
  uploadVersion: (id: number, form: FormData) => request<Contract>(`/api/contracts/${id}/versions`, { method: "POST", body: form }),

  approvals: (id: number) => request<ApprovalRequest[]>(`/api/contracts/${id}/approvals`),
  submitApproval: (id: number, roles: string[], note: string) =>
    json<ApprovalRequest>(`/api/contracts/${id}/approvals`, "POST", { roles, note }),
  decide: (requestId: number, decision: "approve" | "reject", comment: string) =>
    json<ApprovalRequest>(`/api/approvals/${requestId}/decision`, "POST", { decision, comment }),
  withdraw: (requestId: number) => json<ApprovalRequest>(`/api/approvals/${requestId}/withdraw`, "POST"),
  inbox: () => request<InboxItem[]>("/api/approvals/inbox"),

  obligations: (query = "") => request<Obligation[]>(`/api/obligations${query}`),
  createObligation: (contractId: number, values: Partial<Obligation>) =>
    json<Obligation>(`/api/contracts/${contractId}/obligations`, "POST", values),
  updateObligation: (id: number, values: Partial<Obligation>) => json<Obligation>(`/api/obligations/${id}`, "PATCH", values),
  deleteObligation: (id: number) => json<{ deleted: boolean }>(`/api/obligations/${id}`, "DELETE"),

  comments: (contractId: number) => request<Comment[]>(`/api/contracts/${contractId}/comments`),
  addComment: (contractId: number, body: string) => json<Comment>(`/api/contracts/${contractId}/comments`, "POST", { body }),
  deleteComment: (id: number) => json<{ deleted: boolean }>(`/api/comments/${id}`, "DELETE"),

  vendors: () => request<VendorSummary[]>("/api/vendors"),
  report: (groupBy: string, dateField: string, start: string, end: string) => {
    const params = new URLSearchParams({ group_by: groupBy, date_field: dateField, ...(start ? { start } : {}), ...(end ? { end } : {}) });
    return request<Report>(`/api/reports/summary?${params}`);
  },

  templates: (includeInactive = false) => request<Template[]>(`/api/templates${includeInactive ? "?include_inactive=true" : ""}`),
  templatePlaceholders: () => request<{ placeholders: string[]; default_fields: string[] }>("/api/templates/placeholders"),
  createTemplate: (values: Partial<Template>) => json<Template>("/api/templates", "POST", values),
  updateTemplate: (id: number, values: Partial<Template>) => json<Template>(`/api/templates/${id}`, "PATCH", values),
  createContract: (values: Record<string, unknown>) => json<Contract>("/api/contracts", "POST", values),

  aiStatus: () => request<{ configured: boolean; model: string }>("/api/ai/status"),
  aiReview: (id: number) => request<AiReview | null>(`/api/contracts/${id}/ai/review`),
  runAiReview: (id: number) => json<AiReview>(`/api/contracts/${id}/ai/review`, "POST"),
  aiQuestions: (id: number) => request<AiQuestion[]>(`/api/contracts/${id}/ai/questions`),
  askAi: (id: number, question: string) => json<AiQuestion>(`/api/contracts/${id}/ai/questions`, "POST", { question }),
  aiCompare: (id: number, from_version: number, to_version: number) =>
    json<AiComparison>(`/api/contracts/${id}/ai/compare`, "POST", { from_version, to_version }),
  aiSearch: (query: string) => json<AiFilters>("/api/ai/search", "POST", { query }),

  notifications: () => request<{ unread: number; items: Notification[] }>("/api/notifications"),
  readNotification: (id: number) => json<{ read: boolean }>(`/api/notifications/${id}/read`, "POST"),
  readAllNotifications: () => json<{ read: boolean }>("/api/notifications/read-all", "POST"),
  runReminders: () => json<{ created: number }>("/api/reminders/run", "POST"),

  directory: () => request<DirectoryEntry[]>("/api/users/directory"),
  users: () => request<User[]>("/api/users"),
  createUser: (values: { email: string; name: string; role: string; password: string }) => json<User>("/api/users", "POST", values),
  updateUser: (id: number, values: Partial<User> & { password?: string }) => json<User>(`/api/users/${id}`, "PATCH", values),
};

export function can(user: User | null, permission: Permission) {
  return !!user && user.permissions.includes(permission);
}
