import axios from "axios";
import type {
  BmiResponse,
  ChatResponse,
  ChatStreamEvent,
  CookingMethodsResponse,
  EnergyTargetResponse,
  ExerciseCalcResponse,
  ExerciseItem,
  FoodItem,
  FoodLookupResponse,
  IntakePreview,
  LogItem,
  MealType,
  ChatHistoryResponse,
  ChatSessionSummary,
  ProfileResponse,
  ProfileUpdate,
  RangeResponse,
  RecipeBrief,
  RecipeCalcResponse,
  RecommendResponse,
  WeightItem,
  SummaryResponse,
  WeightTrendResponse,
} from "./types";

/**
 * 统一走 Vite 代理的相对路径，不硬编码 127.0.0.1:8000。
 * 换部署域名时只改 vite proxy 一处。
 */
export const http = axios.create({ baseURL: "/api", timeout: 30000 });

http.interceptors.response.use(
  (res) => res,
  (err) => {
    // 409 是业务分支（命中歧义候选），原样抛出让调用方处理
    if (err?.response?.status === 409) return Promise.reject(err);
    const detail = err?.response?.data?.detail;
    const msg =
      (typeof detail === "string" && detail) ||
      (detail && JSON.stringify(detail)) ||
      err?.message ||
      "请求失败，请确认后端服务已启动（python -m uvicorn app.main:app --port 8000）";
    return Promise.reject(new Error(msg));
  },
);

export const todayStr = (): string => {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};

export const api = {
  health: () => http.get("/health").then((r) => r.data),

  // ── 查询 ──
  lookupFood: (name: string) =>
    http.get(`/foods/lookup/${encodeURIComponent(name)}`).then((r) => r.data as FoodLookupResponse),

  searchFoods: (keyword: string, limit = 15) =>
    http
      .get("/foods/search", { params: { keyword, limit } })
      .then((r) => r.data as FoodItem[]),

  getFood: (id: number) => http.get(`/foods/${id}`).then((r) => r.data as FoodItem),

  calcFoodKcal: (name: string, grams: number) =>
    http.post("/foods/kcal", { name, grams }).then((r) => r.data),

  listRecipes: (params: { category?: string; limit?: number } = {}) =>
    http.get("/recipes", { params }).then((r) => r.data as RecipeBrief[]),

  searchRecipes: (keyword: string, limit = 15) =>
    http.get("/recipes/search", { params: { keyword, limit } }).then((r) => r.data as RecipeBrief[]),

  calcRecipe: (payload: {
    name?: string;
    recipe_id?: number;
    grams?: number;
    cooking_method?: string;
    oil_level?: string;
  }) => http.post("/recipes/calc", payload).then((r) => r.data as RecipeCalcResponse),

  cookingMethods: () =>
    http.get("/cooking-methods").then((r) => r.data as CookingMethodsResponse),

  searchExercises: (keyword: string, limit = 15) =>
    http.get("/exercises/search", { params: { keyword, limit } }).then((r) => r.data as ExerciseItem[]),

  dailyExercises: (limit = 50) =>
    http.get("/exercises/daily", { params: { limit } }).then((r) => r.data as ExerciseItem[]),

  calcExercise: (payload: {
    name?: string;
    exercise_id?: number;
    duration_min?: number;
    quantity?: number;
    unit?: string;
  }) => http.post("/exercises/calc", payload).then((r) => r.data as ExerciseCalcResponse),

  recommend: (params: {
    max_kcal?: number;
    min_protein?: number;
    limit?: number;
    category?: string;
    /** 「换一批」用 */
    offset?: number;
  }) => http.get("/recommend", { params }).then((r) => r.data as RecommendResponse),

  // ── 记录 ──
  previewIntake: (payload: {
    name?: string;
    food_id?: number;
    recipe_id?: number;
    grams: number;
    cooking_method?: string;
    oil_level?: string;
  }) => http.post("/intake/preview", payload).then((r) => r.data as IntakePreview),

  createIntake: (payload: {
    name?: string;
    food_id?: number;
    recipe_id?: number;
    grams: number;
    meal_type?: MealType;
    cooking_method?: string;
    oil_level?: string;
    log_date?: string;
  }) => http.post("/intake", payload).then((r) => r.data as LogItem),

  listIntake: (date?: string) =>
    http.get("/intake", { params: { date } }).then((r) => r.data as LogItem[]),

  updateIntake: (id: number, grams: number) =>
    http.patch(`/intake/${id}`, { grams }).then((r) => r.data as LogItem),

  deleteIntake: (id: number) => http.delete(`/intake/${id}`).then((r) => r.data),

  createExerciseLog: (payload: {
    name?: string;
    exercise_id?: number;
    duration_min?: number;
    quantity?: number;
    unit?: string;
    log_date?: string;
  }) => http.post("/exercise-logs", payload).then((r) => r.data as LogItem),

  listExerciseLogs: (date?: string) =>
    http.get("/exercise-logs", { params: { date } }).then((r) => r.data as LogItem[]),

  deleteExerciseLog: (id: number) => http.delete(`/exercise-logs/${id}`).then((r) => r.data),

  recordWeight: (weight_kg: number, log_date?: string) =>
    http.post("/weight", { weight_kg, log_date }).then((r) => r.data as WeightItem),

  listWeight: (days = 90) =>
    http.get("/weight", { params: { days } }).then((r) => r.data as WeightItem[]),

  // ── 看板 ──
  summary: (date?: string) =>
    http.get("/summary", { params: { date } }).then((r) => r.data as SummaryResponse),

  summaryRange: (days = 7) =>
    http.get("/summary/range", { params: { days } }).then((r) => r.data as RangeResponse),

  energyTarget: () => http.get("/energy-target").then((r) => r.data as EnergyTargetResponse),

  weightTrend: (days = 30) =>
    http.get("/weight/trend", { params: { days } }).then((r) => r.data as WeightTrendResponse),

  bmi: (params: { weight_kg?: number; height_cm?: number } = {}) =>
    http.get("/bmi", { params }).then((r) => r.data as BmiResponse),

  getProfile: () => http.get("/profile").then((r) => r.data as ProfileResponse),

  updateProfile: (payload: ProfileUpdate) =>
    http.put("/profile", payload).then((r) => r.data as ProfileResponse),

  meta: () => http.get("/meta").then((r) => r.data),

  // 体重：修改与删除指定日期的记录
  updateWeight: (date: string, payload: { weight_kg: number; body_fat_pct?: number }) =>
    http
      .put(`/weight/${date}`, payload)
      .then((r) => r.data as WeightItem),

  deleteWeight: (date: string) =>
    http.delete(`/weight/${date}`).then((r) => r.data as { deleted: boolean }),

  // ── 会话历史（切页回来要能拿回之前的对话）──
  chatSessions: (limit = 20) =>
    http.get("/chat/sessions", { params: { limit } }).then((r) => r.data as ChatSessionSummary[]),

  chatMessages: (sessionId: string) =>
    http
      .get(`/chat/session/${encodeURIComponent(sessionId)}/messages`)
      .then((r) => r.data as ChatHistoryResponse),
};

// ── 对话：同步 ─────────────────────────────────────────

export function chat(message: string, sessionId?: string, useLlm = true): Promise<ChatResponse> {
  return http
    .post("/chat", { message, session_id: sessionId, use_llm: useLlm })
    .then((r) => r.data as ChatResponse);
}

// ── 对话：SSE 流式 ─────────────────────────────────────

/**
 * 用 fetch + ReadableStream 手动解析 SSE，不用 EventSource ——
 * EventSource 只支持 GET，无法携带请求体，而对话必须走 POST。
 */
export async function chatStream(
  message: string,
  sessionId: string | undefined,
  onEvent: (e: ChatStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
    signal,
  });

  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    throw new Error(`流式请求失败 ${res.status}: ${text.slice(0, 200)}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() ?? "";

    for (const block of blocks) {
      for (const line of block.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (!payload || payload === "[DONE]") continue;
        try {
          onEvent(JSON.parse(payload) as ChatStreamEvent);
        } catch {
          // 心跳行或半包，忽略
        }
      }
    }
  }
}

/** 从 409 响应里取出歧义候选（红线四：同名异物必须让用户选） */
export function extractAmbiguity(err: unknown): string[] {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } } };
  if (e?.response?.status !== 409) return [];
  const detail = e.response?.data?.detail as
    | { candidates?: Array<{ name: string; kcal_per_100g: number | null }> }
    | undefined;
  return (detail?.candidates ?? []).map((c) =>
    c.kcal_per_100g == null ? c.name : `${c.name} · ${c.kcal_per_100g} 千卡/100g`,
  );
}
