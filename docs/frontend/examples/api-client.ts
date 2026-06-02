export interface SessionResponse {
  session_id: string;
  message: string;
  capabilities: {
    streaming: boolean;
    results: boolean;
    diagnostics: boolean;
    resource_reload: boolean;
  };
}

export interface RunCreatedResponse {
  run_id: string;
  session_id: string;
  status: "queued" | "running" | "completed" | "failed";
  events_url: string;
}

export interface RuntimeEvent {
  type: "runtime_event";
  run_id: string;
  sequence: number;
  timestamp: string;
  payload: {
    kind: string;
    payload: Record<string, unknown>;
    error?: string;
  };
}

export interface ResultCreatedEvent {
  type: "result_created";
  run_id: string;
  sequence: number;
  timestamp: string;
  result_id: string;
  sample_rows: Record<string, unknown>[];
  row_count: number;
  has_more: boolean;
}

export interface ModelDeltaEvent {
  type: "model_delta";
  run_id: string;
  sequence: number;
  timestamp: string;
  payload: {
    kind: string;
    stage?: string;
    title?: string;
    model?: string;
    delta: string;
  };
}

export interface RunCompleteEvent {
  type: "run_complete";
  run_id: string;
  session_id: string;
  sequence: number;
  timestamp: string;
  answer: string;
  result_ids: string[];
  diagnostic_run_id?: string;
}

export interface RunErrorEvent {
  type: "run_error";
  run_id: string;
  session_id: string;
  sequence: number;
  timestamp: string;
  error: string;
  message: string;
  diagnostic_run_id?: string;
}

export type AgentWeaveSseEvent =
  | RuntimeEvent
  | ResultCreatedEvent
  | ModelDeltaEvent
  | RunCompleteEvent
  | RunErrorEvent;

export interface ResultPage {
  result_id: string;
  page: number;
  page_size: number;
  total_rows: number;
  row_count_is_exact: boolean;
  has_more: boolean;
  columns: string[];
  rows: Record<string, unknown>[];
  sql: string;
  download_url?: string;
}

export interface DiagnosticRun {
  run_id: string;
  session_id: string;
  summary: Record<string, unknown>;
  model_calls: Record<string, unknown>[];
  events: Record<string, unknown>[];
  timeline: Record<string, unknown>[];
  diagnostic_issues: Record<string, unknown>[];
}

export class AgentWeaveClient {
  constructor(
    private readonly baseUrl: string,
    private readonly token: string,
  ) {}

  async createSession(sessionId?: string): Promise<SessionResponse> {
    return this.post<SessionResponse>("/sessions", {
      session_id: sessionId ?? "",
      metadata: { client: "agentweave-web" },
    });
  }

  async createRun(sessionId: string, message: string): Promise<RunCreatedResponse> {
    return this.post<RunCreatedResponse>(`/sessions/${sessionId}/runs`, {
      message,
      max_turns: 10,
      metadata: { client: "agentweave-web" },
    });
  }

  async getRun(runId: string): Promise<Record<string, unknown>> {
    return this.get<Record<string, unknown>>(`/runs/${runId}`);
  }

  async getResult(
    resultId: string,
    page = 1,
    pageSize = 100,
  ): Promise<ResultPage> {
    return this.get<ResultPage>(
      `/results/${resultId}?page=${page}&page_size=${pageSize}`,
    );
  }

  async getDiagnostics(runId: string): Promise<DiagnosticRun> {
    return this.get<DiagnosticRun>(`/diagnostics/${runId}`);
  }

  async reloadResources(reason = "manual"): Promise<Record<string, unknown>> {
    return this.post<Record<string, unknown>>("/resources/reload", { reason });
  }

  private async get<T>(path: string): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      headers: this.headers(),
    });
    return this.parse<T>(response);
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method: "POST",
      headers: {
        ...this.headers(),
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
    return this.parse<T>(response);
  }

  private headers(): Record<string, string> {
    return { Authorization: `Bearer ${this.token}` };
  }

  private async parse<T>(response: Response): Promise<T> {
    if (!response.ok) {
      const text = await response.text();
      throw new Error(`AgentWeave API ${response.status}: ${text}`);
    }
    return (await response.json()) as T;
  }
}
