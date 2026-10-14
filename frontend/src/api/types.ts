/**
 * 后端接口类型定义。
 *
 * 字段名严格对齐 app/schemas/api.py 与各路由的实际返回 ——
 * 已逐个接口抓过真实响应核对（tdee_value 而非 tdee、
 * recommend 返回 {items,sql} 包裹、weight 用 moving_avg 而非 ma7）。
 */

export interface FoodItem {
  id: number;
  name: string;
  display_name: string;
  kcal_per_100g: number | null;
  protein_per_100g?: number | null;
  fat_per_100g?: number | null;
  carb_per_100g?: number | null;
  fiber_per_100g?: number | null;
  sodium_mg?: number | null;
  calcium_mg?: number | null;
  iron_mg?: number | null;
  category?: string | null;
  source?: string;
  source_ref?: string | null;
  is_ambiguous?: boolean;
  quality_flag?: string;
}

export interface FoodLookupResponse {
  found: boolean;
  need_disambiguation: boolean;
  /** 四级降级匹配路径：alias / exact / base_name / fuzzy */
  match_type?: string | null;
  food?: FoodItem | null;
  candidates: FoodItem[];
  message?: string;
}

export interface RecipeBrief {
  id: number;
  name: string;
  category?: string | null;
  base_serving_g?: number | null;
  default_cooking?: string | null;
}

export interface RecipeBreakdownItem {
  name: string;
  grams: number;
  kcal_per_100g: number | null;
  kcal: number | null;
  is_optional: boolean;
}

export interface CookingOption {
  method: string;
  label: string;
  multiplier: number;
  oil_grams: number;
  needs_oil_level: boolean;
  description?: string;
}

export interface OilLevelOption {
  level: string;
  multiplier: number;
  oil_grams: number;
}

export interface RecipeCalcResponse {
  recipe_id: number;
  recipe_name: string;
  base_serving_g: number | null;
  base_kcal: number | null;
  base_kcal_per_100g: number | null;
  final_kcal_per_100g: number | null;
  cooking_method: string | null;
  oil_level: string | null;
  multiplier: number;
  cooking_note?: string | null;
  breakdown: RecipeBreakdownItem[];
  /** 配料含估算值（如用油量）时为 true */
  is_estimated: boolean;
  grams?: number | null;
  kcal?: number | null;
  /** 信息不足：需要用户补烹饪方式或油量 */
  need_clarify?: boolean;
  clarify_message?: string | null;
  /** 待选烹饪方式（未指定做法时返回） */
  clarify_options?: CookingOption[];
  oil_level_options?: OilLevelOption[];
}

export interface CookingMethodsResponse {
  methods: CookingOption[];
  oil_levels: OilLevelOption[];
  /** 倍率的数据出处，界面上要展示 */
  basis?: string;
  note?: string;
}

export interface ExerciseItem {
  id: number;
  name: string;
  category_l1?: string | null;
  activity_name?: string | null;
  intensity?: string | null;
  met: number | null;
  is_daily_activity?: boolean;
}

export interface ExerciseCalcResponse {
  exercise?: ExerciseItem | null;
  met: number | null;
  weight_kg?: number | null;
  duration_min?: number | null;
  quantity?: number | null;
  unit?: string | null;
  kcal: number | null;
  note?: string | null;
  is_daily?: boolean;
  need_disambiguation?: boolean;
  candidates?: ExerciseItem[];
}

// ── 记录类 ─────────────────────────────────────────────

export type MealType = "breakfast" | "lunch" | "dinner" | "snack";

export const MEAL_LABEL: Record<MealType, string> = {
  breakfast: "早餐",
  lunch: "午餐",
  dinner: "晚餐",
  snack: "加餐",
};

export interface LogItem {
  id: number;
  name: string;
  grams?: number | null;
  kcal: number;
  meal_type?: MealType | null;
  log_date?: string | null;
  cooking_method?: string | null;
  oil_level?: string | null;
  is_daily_activity?: boolean | null;
  duration_min?: number | null;
  /** 后端记录的完整计算过程，前端可展开查看 */
  calc_detail?: string | null;
  confirmed?: boolean;
  created_at?: string | null;
}

export interface IntakePreview {
  kind: "food" | "recipe";
  food_id?: number;
  recipe_id?: number;
  name: string;
  grams: number;
  kcal: number;
  cooking_method?: string | null;
  oil_level?: string | null;
  detail: string;
  confirm_prompt: string;
}

// ── 看板类 ─────────────────────────────────────────────

export interface SummaryResponse {
  date: string;
  intake_kcal: number;
  /** 运动额外消耗（不含 BMR） */
  exercise_kcal: number;
  bmr: number | null;
  tdee_value: number | null;
  target_kcal: number | null;
  /** 负数表示缺口，正数表示盈余 */
  balance: number;
  remaining: number;
  intake_count: number;
  exercise_count: number;
  status: string;
  target_warnings: string[];
  /** BMR 与 TDEE 的概念澄清，必须在界面上展示 */
  concept_note?: string;
}

export interface RangePoint {
  date: string;
  intake_kcal: number;
  exercise_kcal: number;
}

export interface RangeResponse {
  start: string;
  days: number;
  points: RangePoint[];
}

/** BMR 的计算依据，必须能追溯 */
export interface BMRSources {
  weight_kg: number;
  weight_date: string | null;
  weight_source: "latest_log" | "profile";
  height_cm: number;
  age: number;
  gender: string;
  formula: string;
  expression: string;
  bmr: number;
  tdee: number;
  activity_level: number;
  accuracy_note: string;
}

export interface ProfileResponse {
  id: number;
  gender: string;
  age: number;
  height_cm: number;
  weight_kg: number;
  activity_level: number;
  goal: string;
  target_weight_kg: number | null;
  daily_calorie_target: number | null;
  bmr_sources?: BMRSources | null;
}

export interface ProfileUpdate {
  gender?: "male" | "female";
  age?: number;
  height_cm?: number;
  weight_kg?: number;
  activity_level?: number;
  goal?: "lose" | "maintain" | "gain";
  target_weight_kg?: number | null;
}

export interface EnergyTargetResponse {
  bmr: number;
  tdee_value: number;
  target_kcal: number;
  warnings: string[];
  formula_note?: string;
  estimate_note?: string;
}

export interface WeightItem {
  log_date: string;
  weight_kg: number;
  bmi_value?: number | null;
  bmi_category?: string | null;
  bmi_standard?: string | null;
  body_fat_pct?: number | null;
  /** 7 日移动平均 —— 必须画出来，只画原始值会看到噪音 */
  moving_avg?: number | null;
}

export interface WeightTrendResponse {
  points: WeightItem[];
  start_ma?: number | null;
  end_ma?: number | null;
  change_kg?: number | null;
  change_pct?: number | null;
  message?: string;
}

export interface GoalPlan {
  total_deficit_kcal: number;
  daily_deficit_kcal: number;
  weekly_rate_kg: number;
  feasible: boolean;
  note?: string;
  current_daily_deficit?: number;
  match?: boolean;
}

export interface BmiResponse {
  weight_kg: number;
  height_cm: number;
  bmi: number;
  category: string;
  standard: string;
  who_category: string;
  formula: string;
  note?: string;
  goal_plan?: GoalPlan;
}

// ── 推荐 ───────────────────────────────────────────────

export interface RecommendItem {
  name: string;
  kcal_per_100g: number;
  protein_per_100g: number | null;
  fiber_per_100g?: number | null;
  category?: string | null;
  /** 蛋白质密度：蛋白 g / 100kcal，越高越扛饿 */
  protein_density: number | null;
  why?: string;
}

export interface RecommendResponse {
  items: RecommendItem[];
  max_kcal: number;
  min_protein: number;
  note?: string;
  /** 实际执行的 SQL —— 证明这是结构化查询而非向量检索 */
  sql?: string;
  /** 满足条件的食物总数 */
  total: number;
  /** 本次跳过的条数 */
  offset: number;
  /** 还有没有下一批 —— 前端据此决定「换一批」是否可用 */
  has_more: boolean;
}

// ── 对话 ───────────────────────────────────────────────

export interface ChatCitation {
  type?: string;
  id?: number;
  // name 不是所有引用类型都有（recommend/exercise_recommend 曾只给 items），
  // 但前端曾按必填处理并直接 .slice() —— 字段缺失即整页白屏。
  // 这里标为可选，强制调用方做兜底。
  name?: string;
  items?: string[];
}

export interface ChatResponse {
  intent: string;
  answer: string;
  needs_clarification: boolean;
  clarify_message?: string | null;
  clarify_options?: Array<{ value: string; label: string }>;
  tool_results?: Array<Record<string, unknown>>;
  citations?: ChatCitation[];
  session_id: string;
  /** 数值校验警告：模型输出了未经数据库核实的数字 */
  guard_warning?: string | null;
  errors?: string[];
}

/**
 * SSE 事件。实测后端统一是 {type, data} 嵌套结构。
 * token 事件目前是整段内容一次性推送，前端按追加处理。
 */
export type ChatStreamEvent =
  | { type: "intent"; data: { intent: string; label?: string; session_id: string } }
  | { type: "tool"; data: { results?: Array<Record<string, unknown>> } }
  | { type: "clarify"; data: { message: string; options?: Array<{ value: string; label: string }> } }
  | { type: "token"; data: { content: string } }
  | {
      type: "done";
      data: {
        answer?: string;
        citations?: ChatCitation[];
        guard_warning?: string | null;
        errors?: string[];
        session_id?: string;
      };
    }
  | { type: "error"; data: { message: string } };

// ── 会话历史 ─────────────────────────────────────────────

export interface ChatSessionSummary {
  thread_id: string;
  title: string | null;
  message_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface ChatHistoryMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  intent: string | null;
  created_at: string | null;
  citations?: ChatCitation[];
}

export interface ChatHistoryResponse {
  session_id: string;
  title?: string | null;
  messages: ChatHistoryMessage[];
}
