import type {
  CancelRCAJobResponse,
  CreateRCAJobRequest,
  KnowledgeApprovalRequest,
  KnowledgeIngestionListResponse,
  KnowledgeIngestionResponse,
  KnowledgeLookupRequest,
  KnowledgeLookupResult,
  MemoryApprovalRequest,
  MemoryCandidateResponse,
  RCAJobCreated,
  RCAJobResponse,
  RCAReportResponse,
  RuntimeInfo,
} from "./types";
import type { RCAJobEvent } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers);
  if (!(options?.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as
      | { detail?: string | { error_code?: string; message?: string } }
      | null;
    const detail = payload?.detail;
    const message = typeof detail === "string"
      ? detail
      : [detail?.error_code, detail?.message].filter(Boolean).join(": ") ||
        `Request failed with status ${response.status}`;
    throw new Error(message);
  }

  return (await response.json()) as T;
}

export function createRCAJob(payload: CreateRCAJobRequest): Promise<RCAJobCreated> {
  return request<RCAJobCreated>("/rca/jobs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getRCAJob(jobId: string): Promise<RCAJobResponse> {
  return request<RCAJobResponse>(`/rca/jobs/${jobId}`);
}

export function getRCAReport(jobId: string): Promise<RCAReportResponse> {
  return request<RCAReportResponse>(`/rca/jobs/${jobId}/report`);
}

export function cancelRCAJob(jobId: string): Promise<CancelRCAJobResponse> {
  return request<CancelRCAJobResponse>(`/rca/jobs/${jobId}/cancel`, {
    method: "POST",
  });
}

export function getJobMemoryCandidate(jobId: string): Promise<MemoryCandidateResponse> {
  return request<MemoryCandidateResponse>(`/rca/jobs/${jobId}/memory-candidate`);
}

export function openRCAJobEventStream(
  jobId: string,
  onEvent: (event: RCAJobEvent) => void,
  onConnectionChange: (state: "live" | "reconnecting") => void,
  afterSequence = 0,
): EventSource {
  const query = afterSequence > 0 ? `?after=${afterSequence}` : "";
  const source = new EventSource(
    `${API_BASE}/rca/jobs/${encodeURIComponent(jobId)}/events${query}`,
  );
  source.onopen = () => onConnectionChange("live");
  source.addEventListener("job_event", (rawEvent) => {
    const message = rawEvent as MessageEvent<string>;
    onEvent(JSON.parse(message.data) as RCAJobEvent);
  });
  source.onerror = () => onConnectionChange("reconnecting");
  return source;
}

export function getRuntimeInfo(): Promise<RuntimeInfo> {
  return request<RuntimeInfo>("/ready");
}

export function getMemoryCandidate(candidateId: string): Promise<MemoryCandidateResponse> {
  return request<MemoryCandidateResponse>(`/memory/candidates/${candidateId}`);
}

export function decideMemoryCandidate(
  candidateId: string,
  payload: MemoryApprovalRequest,
): Promise<MemoryCandidateResponse> {
  return request<MemoryCandidateResponse>(
    `/memory/candidates/${candidateId}/approvals`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

export function lookupKnowledge(
  payload: KnowledgeLookupRequest,
): Promise<KnowledgeLookupResult> {
  return request<KnowledgeLookupResult>("/knowledge/lookups", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function ingestKnowledge(formData: FormData): Promise<KnowledgeIngestionResponse> {
  return request<KnowledgeIngestionResponse>("/knowledge/ingestions", {
    method: "POST",
    body: formData,
  });
}

export function listKnowledgeIngestions(
  status?: KnowledgeIngestionResponse["candidate"]["status"],
): Promise<KnowledgeIngestionListResponse> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return request<KnowledgeIngestionListResponse>(`/knowledge/ingestions${query}`);
}

export function decideKnowledgeIngestion(
  candidateId: string,
  payload: KnowledgeApprovalRequest,
): Promise<KnowledgeIngestionResponse> {
  return request<KnowledgeIngestionResponse>(
    `/knowledge/ingestions/${candidateId}/approvals`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}
