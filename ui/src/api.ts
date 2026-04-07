/**
 * Typed fetch wrappers for the scathach FastAPI backend.
 *
 * In production (Tauri), the port is injected by the Rust shell as
 *   window.__SCATHACH_API_PORT__
 * In development (Vite dev server), calls are proxied via /api → localhost:8765.
 */

declare global {
  interface Window {
    __SCATHACH_API_PORT__?: number;
  }
}

function baseUrl(): string {
  if (typeof window !== "undefined" && window.__SCATHACH_API_PORT__) {
    return `http://127.0.0.1:${window.__SCATHACH_API_PORT__}`;
  }
  // Vite dev proxy strips /api prefix
  return "/api";
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  isFormData = false
): Promise<T> {
  const url = `${baseUrl()}${path}`;
  const headers: Record<string, string> = {};
  if (body && !isFormData) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(url, {
    method,
    headers,
    body: body
      ? isFormData
        ? (body as FormData)
        : JSON.stringify(body)
      : undefined,
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`API ${method} ${path} → ${res.status}: ${detail}`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

const get = <T>(path: string) => request<T>("GET", path);
const post = <T>(path: string, body?: unknown) => request<T>("POST", path, body);
const patch = <T>(path: string, body: unknown) => request<T>("PATCH", path, body);
const del = <T>(path: string) => request<T>("DELETE", path);

// ---------------------------------------------------------------------------
// Types (mirrors scathach/api/models.py)
// ---------------------------------------------------------------------------

export interface Topic {
  id: number;
  name: string;
  source_path: string | null;
  created_at: string;
}

export interface Question {
  id: number;
  topic_id: number;
  difficulty: number;
  body: string;
  parent_id: number | null;
  is_root: boolean;
}

export interface QuestionContext {
  index: number;
  total: number;
  depth: number;
  is_timed: boolean;
  started_at: string;
}

export interface SessionSummary {
  session_id: string;
  topic_id: number;
  topic_name: string;
  status: string;
  timing: string;
  threshold: number;
  num_levels: number;
  cleared_count: number;
  total_questions: number;
  created_at: string;
  updated_at: string;
}

export interface SessionCreateResponse {
  session_id: string;
  topic_id: number;
  question: Question;
  context: QuestionContext;
}

export interface AnswerResult {
  raw_score: number;
  final_score: number;
  passed: boolean;
  time_penalty: boolean;
  diagnosis: string;
  ideal_answer: string;
  next_question: Question | null;
  next_context: QuestionContext | null;
  hydra_spawned: boolean;
  subquestion_count: number;
  is_complete: boolean;
  cleared_count: number | null;
  total_attempts: number | null;
}

export interface ReviewQueueResponse {
  questions: Question[];
  queue: string;
  total_due: number;
}

export interface ReviewAnswerResult {
  raw_score: number;
  final_score: number;
  passed: boolean;
  time_penalty: boolean;
  diagnosis: string;
  ideal_answer: string;
  next_review_at: string | null;
}

export interface Config {
  model: string;
  quality_threshold: number;
  main_timing: string;
  review_timing: string;
  hydra_in_super_review: boolean;
  open_doc_on_session: boolean;
  has_api_key: boolean;
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export const api = {
  health: () => get<{ status: string }>("/health"),

  // Topics
  listTopics: () => get<{ topics: Topic[] }>("/topics"),
  ingestFile: async (file: File): Promise<Topic> => {
    const fd = new FormData();
    fd.append("file", file);
    return request<Topic>("POST", "/topics/ingest", fd, true);
  },
  ingestPaste: (text: string, topicName: string) =>
    post<Topic>("/topics/paste", { text, topic_name: topicName }),
  renameTopic: (topicId: number, newName: string) =>
    patch<Topic>(`/topics/${topicId}`, { new_name: newName }),

  // Sessions
  createSession: (
    topicId: number,
    timing: string,
    threshold: number,
    numLevels: number
  ) =>
    post<SessionCreateResponse>("/sessions", {
      topic_id: topicId,
      timing,
      threshold,
      num_levels: numLevels,
    }),
  listSessions: () => get<SessionSummary[]>("/sessions"),
  getSession: (sessionId: string) =>
    get<SessionSummary>(`/sessions/${sessionId}`),
  submitAnswer: (
    sessionId: string,
    answerText: string,
    elapsedS?: number
  ) =>
    post<AnswerResult>(`/sessions/${sessionId}/answer`, {
      answer_text: answerText,
      elapsed_s: elapsedS ?? null,
    }),
  abandonSession: (sessionId: string) =>
    del<void>(`/sessions/${sessionId}`),

  // Review
  getDueQuestions: (
    queue: string,
    mode: string,
    limit = 20
  ) =>
    get<ReviewQueueResponse>(
      `/review/due?queue=${queue}&mode=${mode}&limit=${limit}`
    ),
  submitReviewAnswer: (
    questionId: number,
    answerText: string,
    queue: string,
    timed: boolean,
    elapsedS?: number
  ) =>
    post<ReviewAnswerResult>(`/review/${questionId}/answer`, {
      answer_text: answerText,
      queue,
      timed,
      elapsed_s: elapsedS ?? null,
    }),

  // Config
  getConfig: () => get<Config>("/config"),
  patchConfig: (patch: Partial<Config & { api_key?: string }>) =>
    request<Config>("PATCH", "/config", patch),
  testConfig: () => post<{ ok: boolean; message: string }>("/config/test"),
};
