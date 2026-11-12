export type StepState = "done" | "todo" | "attention";

export interface Producer {
  name: string;
  slug: string;
  commission: number;
  contact_email: string | null;
  patterns: string[];
  labels: string[];
  channels: string[];
}

export interface Vertical {
  key: string;
  label: string;
  shorts_mode: "pool" | "per_channel";
  producers: Producer[];
}

export interface Meta {
  network: string;
  currency: string;
  demo: boolean;
  expires_at: number | null;
  periods: { period: string; label: string }[];
  withholding: Record<string, number>;
  verticals: Vertical[];
}

export interface SettlementLine {
  Producer: string;
  Revenue: number;
  "Shorts Share": number;
  "Other Earnings": number;
  "Withholding Tax": number;
  "Net Revenue": number;
  "Commission Rate"?: number;
  "Network Commission": number;
  "Partner Payment": number;
  Vertical?: string;
}

export interface Unmatched {
  "Asset ID": string;
  "Custom ID": string;
  "Asset Labels": string;
  "Asset Title": string;
  "Asset Channel ID": string;
  Rows: number;
  "Partner Revenue": number;
}

export interface Group {
  kind: "label" | "pattern";
  value: string;
  assets: number;
  revenue: number;
}

export interface FileInfo {
  name: string;
  rows: number;
  revenue: number;
  kind: string;
  error: string | null;
}

export interface Packages {
  generated_at: string;
  stale: boolean;
  clients: { producer: string; slug: string; email: string | null; files: string[]; password: string }[];
  internal: { file: string; password: string };
}

export interface Month {
  period: string;
  label: string;
  currency: string;
  fx_rate: number | null;
  inputs: { payout_local: number | null; fx_rate: number | null; shorts_pool_usd: Record<string, number> };
  totals: {
    revenue: number;
    withholding: number;
    commission: number;
    payment: number;
    unidentified: number;
    unidentified_count: number;
    clients: number;
  };
  files: Record<string, FileInfo[]>;
  verticals: {
    key: string;
    label: string;
    shorts_mode: string;
    settlement: SettlementLine[];
    unidentified: Unmatched[];
    groups: Group[];
  }[];
  combined: SettlementLine[];
  trend: { period: string; revenue: number; payment: number }[];
  steps: Record<"upload" | "match" | "review" | "send", StepState>;
  packages: Packages | null;
}

export interface ClientCard {
  producer: string;
  slug: string;
  email: string | null;
  verticals: string[];
  revenue: number;
  payment: number;
  commission: number;
  previous_payment: number | null;
  history: { period: string; payment: number }[];
}

export interface ClientDetail {
  producer: string;
  slug: string;
  email: string | null;
  channels: string[];
  statement: SettlementLine[];
  totals: Record<string, number>;
  top_assets: { name: string; value: number }[];
  countries: { name: string; value: number }[];
  history: { period: string; value: number }[];
}

export interface Backups {
  snapshots: string[];
  live: boolean;
  latest: null | {
    taken: string;
    channels: Record<string, string | number | boolean | null>[];
    changes: { "Channel ID": string; Change: string; Before: string; After: string }[];
  };
}

export interface Conflict {
  id: string;
  kind: string;
  title: string;
  asset_id: string;
  other_party: string;
  territories: string;
  status: string;
  due: string;
  notified_at: string;
}

export interface TestInfo {
  text: string;
  remaining: number | null;
  channels: {
    telegram: { available: boolean; bot: string | null; has_default: boolean };
    whatsapp: { available: boolean; sender: string | null; join_code: string | null; has_default: boolean };
  };
}

export interface Conflicts {
  conflicts: Conflict[];
  channels: string[];
  test: TestInfo;
  new?: number;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, { credentials: "same-origin", ...init });
  if (!response.ok) {
    let message = `Something went wrong (${response.status}).`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      /* not JSON: keep the generic message */
    }
    throw new Error(message);
  }
  return response.json();
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  meta: () => call<Meta>("/meta"),
  month: (period: string) => call<Month>(`/periods/${period}`),
  createMonth: (period: string) => call<{ period: string }>("/periods", json("POST", { period })),
  upload: (period: string, vertical: string, files: File[]) => {
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    return call<{ saved: string[]; rejected: { name: string; reason: string }[] }>(
      `/periods/${period}/files/${vertical}`,
      { method: "POST", body: form },
    );
  },
  removeFile: (period: string, vertical: string, name: string) =>
    call(`/periods/${period}/files/${vertical}/${encodeURIComponent(name)}`, { method: "DELETE" }),
  saveInputs: (period: string, body: { payout_local: number | null; fx_rate: number | null; shorts_pool_usd: Record<string, number> }) =>
    call(`/periods/${period}/inputs`, json("PUT", body)),
  match: (body: { vertical: string; kind: string; value: string; producer: string; new_commission?: number }) =>
    call("/match", json("POST", body)),
  clients: (period: string) => call<ClientCard[]>(`/periods/${period}/clients`),
  client: (period: string, slug: string) => call<ClientDetail>(`/periods/${period}/clients/${slug}`),
  generate: (period: string) => call<Packages>(`/periods/${period}/packages`, { method: "POST" }),
  deliver: (period: string, only: string) =>
    call<{ producer: string; recipient: string | null; status: string }[]>(`/periods/${period}/deliver`, json("POST", { only })),
  backups: () => call<Backups>("/backups"),
  takeBackup: () => call<Backups>("/backups", { method: "POST" }),
  conflicts: () => call<Conflicts>("/conflicts"),
  checkConflicts: () => call<Conflicts>("/conflicts/check", { method: "POST" }),
  testAlert: (channel: string, to: string) =>
    call<TestInfo & { sent: boolean }>("/conflicts/test", json("POST", { channel, to: to || null })),
  resetDemo: () => call("/demo/reset", { method: "POST" }),
  previewUrl: (period: string, slug: string) => `/api/periods/${period}/clients/${slug}/preview.pdf`,
  downloadUrl: (period: string, owner: string, name: string) =>
    `/api/periods/${period}/download/${owner}/${encodeURIComponent(name)}`,
};
