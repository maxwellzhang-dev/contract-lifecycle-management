export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "/backend-api";

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

async function read<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "Request failed" }));
    throw new Error(body.detail ?? `Request failed (${response.status})`);
  }
  return response.json();
}

export const api = {
  dashboard: (months = 12) => fetch(`${API_BASE}/api/dashboard?months=${months}`).then(read<Dashboard>),
  contracts: (query = "") => fetch(`${API_BASE}/api/contracts${query}`).then(read<Contract[]>),
  audit: () => fetch(`${API_BASE}/api/audit`).then(read<AuditItem[]>),
  update: (id: number, values: Partial<Contract>) =>
    fetch(`${API_BASE}/api/contracts/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    }).then(read<Contract>),
  validate: (id: number) =>
    fetch(`${API_BASE}/api/contracts/${id}/validate`, { method: "POST" }).then(read<Contract>),
  remove: (id: number) =>
    fetch(`${API_BASE}/api/contracts/${id}`, { method: "DELETE" }).then(read<{ deleted: boolean; id: number }>),
  upload: (form: FormData) =>
    fetch(`${API_BASE}/api/contracts/upload`, { method: "POST", body: form }).then(read<Contract>),
};
