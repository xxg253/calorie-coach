import { useMemo, useState } from "react";
import {
  Alert,
  AutoComplete,
  Button,
  Card,
  Col,
  DatePicker,
  Divider,
  Empty,
  Input,
  InputNumber,
  Popconfirm,
  Modal,
  Radio,
  Row,
  Segmented,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
  App as AntApp,
} from "antd";
import {
  CheckCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  ExperimentOutlined,
  PlusOutlined,
  ReloadOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import dayjs, { type Dayjs } from "dayjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, extractAmbiguity, todayStr } from "../api/client";
import type {
  CookingOption,
  FoodItem,
  IntakePreview,
  LogItem,
  MealType,
  OilLevelOption,
  RecipeCalcResponse,
  WeightItem,
} from "../api/types";
import { MEAL_LABEL } from "../api/types";

const { Paragraph, Text } = Typography;

type TabKey = "food" | "exercise" | "weight";

export default function RecordPage() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<TabKey>("food");
  const [date, setDate] = useState<Dayjs>(dayjs(todayStr()));
  const dateStr = date.format("YYYY-MM-DD");

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["summary"] });
    qc.invalidateQueries({ queryKey: ["intake"] });
    qc.invalidateQueries({ queryKey: ["exercise-logs"] });
    qc.invalidateQueries({ queryKey: ["weight"] });
    qc.invalidateQueries({ queryKey: ["trend"] });
    qc.invalidateQueries({ queryKey: ["range"] });
  };

  return (
    <div className="page-stack">
      <Card
        title="记录"
        extra={
          <Space>
            <DatePicker
              value={date}
              onChange={(d) => d && setDate(d)}
              allowClear={false}
              format="YYYY-MM-DD"
            />
            <Tooltip title="重新拉取今日数据">
              <Button icon={<ReloadOutlined />} onClick={invalidate} />
            </Tooltip>
          </Space>
        }
      >
        <Segmented
          value={tab}
          onChange={(v) => setTab(v as TabKey)}
          options={[
            { label: "饮食", value: "food" },
            { label: "运动", value: "exercise" },
            { label: "体重", value: "weight" },
          ]}
          style={{ marginBottom: 16 }}
        />
        {tab === "food" && <FoodPanel dateStr={dateStr} onDone={invalidate} />}
        {tab === "exercise" && <ExercisePanel dateStr={dateStr} onDone={invalidate} />}
        {tab === "weight" && <WeightPanel dateStr={dateStr} onDone={invalidate} />}
      </Card>
    </div>
  );
}

// ─────────────────────────────────────────────────────────
// 饮食
// ─────────────────────────────────────────────────────────

function FoodPanel({ dateStr, onDone }: { dateStr: string; onDone: () => void }) {
  const { message: msg } = AntApp.useApp();
  const [name, setName] = useState("");
  const [grams, setGrams] = useState<number>(200);
  const [mealType, setMealType] = useState<MealType | undefined>("lunch");
  const [method, setMethod] = useState<string | undefined>();
  const [oil, setOil] = useState<string | undefined>();
  const [options, setOptions] = useState<{ value: string; label: string }[]>([]);

  // 预览结果（两阶段流程第一步）
  const [preview, setPreview] = useState<IntakePreview | null>(null);
  // 歧义候选（红线四）：必须让用户选具体那一条
  const [ambiguity, setAmbiguity] = useState<{ open: boolean; name: string; candidates: string[] }>({
    open: false,
    name: "",
    candidates: [],
  });
  const [breakdown, setBreakdown] = useState<RecipeCalcResponse | null>(null);

  const methodsQ = useQuery({ queryKey: ["cooking-methods"], queryFn: () => api.cookingMethods() });
  const intakeQ = useQuery({
    queryKey: ["intake", dateStr],
    queryFn: () => api.listIntake(dateStr),
  });

  // 食物搜索建议
  const foodSuggestQ = useQuery({
    queryKey: ["food-suggest", name],
    queryFn: () => api.searchFoods(name, 12),
    enabled: name.trim().length >= 1,
  });
  const recipeSuggestQ = useQuery({
    queryKey: ["recipe-suggest", name],
    queryFn: () => api.searchRecipes(name, 12),
    enabled: name.trim().length >= 1,
  });

  const foodOptions = useMemo(
    () =>
      (foodSuggestQ.data ?? []).map((f: FoodItem) => ({
        value: f.name,
        label: (
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
            <span>
              {f.name}
              {f.is_ambiguous && (
                <Tag color="warning" style={{ marginLeft: 6 }}>
                  多同名
                </Tag>
              )}
            </span>
            <Text type="secondary">{f.kcal_per_100g ?? "-"} 千卡/100g</Text>
          </div>
        ),
      })),
    [foodSuggestQ.data],
  );

  const recipeOptions = useMemo(
    () =>
      (recipeSuggestQ.data ?? []).map((r) => ({
        value: r.name,
        label: (
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
            <span>{r.name}</span>
            <Tag color="green">菜谱</Tag>
          </div>
        ),
      })),
    [recipeSuggestQ.data],
  );

  const previewMut = useMutation({
    mutationFn: api.previewIntake,
    onSuccess: (d: IntakePreview) => {
      setAmbiguity({ open: false, name: "", candidates: [] });
      setPreview(d);
    },
    onError: (e: Error) => {
      const cands = extractAmbiguity(e);
      if (cands.length) {
        // 红线四：同名异物必须让用户选，绝不静默取第一条或取平均
        setPreview(null);
        setAmbiguity({ open: true, name: name.trim(), candidates: cands });
      } else {
        msg.error(e.message);
      }
    },
  });

  const createMut = useMutation({
    mutationFn: api.createIntake,
    onSuccess: (d: LogItem) => {
      msg.success(`已记录：${d.name} ${d.grams}g = ${d.kcal} 千卡`);
      setPreview(null);
      setBreakdown(null);
      setName("");
      onDone();
    },
    onError: (e: Error) => msg.error(e.message),
  });

  const delMut = useMutation({
    mutationFn: (id: number) => api.deleteIntake(id),
    onSuccess: () => {
      msg.success("已撤销");
      onDone();
    },
    onError: (e: Error) => msg.error(e.message),
  });

  /** 先算菜谱，拿到追问项（烹饪方式 / 油量） */
  const probeRecipe = async () => {
    if (!name.trim()) return msg.warning("请先输入食物或菜名");
    try {
      // 必须把用户已选的烹饪方式与油量传过去，否则后端每次都判定信息不足、
      // 反复返回追问，前端永远进不到确认弹窗。
      const r = await api.calcRecipe({
        name: name.trim(),
        grams,
        cooking_method: method,
        oil_level: oil,
      });
      setBreakdown(r);
      if (r.need_clarify) {
        setPreview(null);
        // 后端同时返回了做法与油量两组选项。若用户还没选做法，
        // 先问做法；做法已定则直接问油量（油量对热量的影响更大）。
        const needMethod = !method;
        if (needMethod) {
          setOptions(
            (r.clarify_options ?? []).map((o: CookingOption) => ({
              value: o.method,
              label: `${o.label} ×${o.multiplier}`,
            })),
          );
        } else {
          setOptions(
            (r.oil_level_options ?? []).map((o: OilLevelOption) => ({
              value: o.level,
              label: `${o.level} ×${o.multiplier}（约 ${o.oil_grams}g 油）`,
            })),
          );
        }
        msg.info(r.clarify_message ?? "请补充信息");
      } else {
        // 做法已明确，直接进入预览确认
        previewMut.mutate({
          name: name.trim(),
          recipe_id: r.recipe_id,
          grams,
          cooking_method: r.cooking_method ?? method,
          oil_level: r.oil_level ?? oil,
        });
      }
    } catch (e) {
      // 不是菜谱就退回当普通食物查
      previewMut.mutate({ name: name.trim(), grams, cooking_method: method, oil_level: oil });
    }
  };

  const oilOptions: OilLevelOption[] = methodsQ.data?.oil_levels ?? [];

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} lg={13}>
        <Card size="small" title="添加饮食">
          <Space orientation="vertical" style={{ width: "100%" }} size={12}>
            <AutoComplete
              value={name}
              onChange={setName}
              options={[...recipeOptions, ...foodOptions]}
              style={{ width: "100%" }}
              placeholder="输入菜名或食物名（如：西红柿炒鸡蛋、白饭、鸡胸肉）"
              filterOption={(input, opt) =>
                String(opt?.value ?? "").toLowerCase().includes(input.toLowerCase())
              }
            >
              <Input.Search
                enterButton={
                  <Button type="primary" icon={<ExperimentOutlined />} onClick={probeRecipe}>
                    计算
                  </Button>
                }
                onSearch={probeRecipe}
              />
            </AutoComplete>

            <Row gutter={12}>
              <Col span={8}>
                <Text className="metric-label">克重（g）</Text>
                <Space.Compact style={{ width: "100%" }}>
                  <InputNumber
                    value={grams}
                    onChange={(v) => setGrams(Number(v) || 0)}
                    min={1}
                    max={3000}
                    style={{ width: "100%" }}
                  />
                  <Button disabled style={{ width: 40, pointerEvents: "none" }}>
                    克
                  </Button>
                </Space.Compact>
              </Col>
              <Col span={8}>
                <Text className="metric-label">餐次</Text>
                <Select
                  value={mealType}
                  onChange={setMealType}
                  allowClear
                  style={{ width: "100%" }}
                  options={Object.entries(MEAL_LABEL).map(([v, l]) => ({ value: v, label: l }))}
                />
              </Col>
              <Col span={8}>
                <Text className="metric-label">烹饪方式</Text>
                <Select
                  value={method}
                  onChange={setMethod}
                  allowClear
                  placeholder="不确定就留空"
                  style={{ width: "100%" }}
                  options={(methodsQ.data?.methods ?? []).map((m) => ({
                    value: m.method,
                    label: `${m.label} ×${m.multiplier}`,
                  }))}
                />
              </Col>
            </Row>

            <div>
              <Text className="metric-label">用油量（仅炒/煎类需要）</Text>
              <Select
                value={oil}
                onChange={setOil}
                allowClear
                placeholder="留空则计算时会追问"
                style={{ width: 200 }}
                options={oilOptions.map((o) => ({
                  value: o.level,
                  label: `${o.level} ×${o.multiplier}（约 ${o.oil_grams}g 油）`,
                }))}
              />
              {options.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  <Text className="metric-label">请选择做法：</Text>
                  <div style={{ marginTop: 4 }}>
                    <Select
                      placeholder={method ? "选择用油量" : "选择烹饪方式"}
                      style={{ width: 240 }}
                      options={options}
                      onChange={(v) => {
                        // 做法还没定就写 method，已定就写 oil_level ——
                        // 两者都是后端判定「信息是否充分」的依据
                        if (!method) setMethod(v);
                        else setOil(v);
                        setOptions([]);
                      }}
                      value={method ?? oil ?? undefined}
                    />
                  </div>
                </div>
              )}
            </div>

            {methodsQ.data?.basis && (
              <Alert
                type="info"
                showIcon
                title={<Text style={{ fontSize: 12 }}>{methodsQ.data.basis}</Text>}
              />
            )}
          </Space>
        </Card>

        {/* 配料明细：让用户看得见热量是怎么算出来的 */}
        {breakdown && breakdown.breakdown?.length > 0 && (
          <Card
            size="small"
            title={`配料明细 · ${breakdown.recipe_name}`}
            style={{ marginTop: 16 }}
            extra={
              breakdown.is_estimated ? <Tag color="orange">含估算值</Tag> : <Tag color="green">精确值</Tag>
            }
          >
            <Table
              size="small"
              rowKey="name"
              pagination={false}
              dataSource={breakdown.breakdown}
              columns={[
                { title: "配料", dataIndex: "name" },
                { title: "克重", dataIndex: "grams", render: (v: number) => `${v}g` },
                {
                  title: "热量/100g",
                  dataIndex: "kcal_per_100g",
                  render: (v: number | null) => (v == null ? "无数据" : v),
                },
                {
                  title: "折算热量",
                  dataIndex: "kcal",
                  render: (v: number | null) => <Text strong>{v ?? "-"}</Text>,
                },
              ]}
            />
            <Paragraph style={{ marginTop: 10, marginBottom: 0 }} type="secondary">
              食材原值 {breakdown.base_kcal_per_100g?.toFixed(1) ?? "-"} 千卡/100g
              {breakdown.multiplier !== 1 && (
                <>
                  {" "}
                  × {breakdown.cooking_method}
                  {breakdown.oil_level ? `（${breakdown.oil_level}）` : ""} 倍率 ×
                  {breakdown.multiplier} ={" "}
                  <Text strong>{breakdown.final_kcal_per_100g?.toFixed(1)}</Text> 千卡/100g
                </>
              )}
            </Paragraph>
          </Card>
        )}
      </Col>

      <Col xs={24} lg={11}>
        <Card
          size="small"
          title={`${dateStr} 的饮食记录`}
          extra={intakeQ.isLoading ? <Spin size="small" /> : <Text>{intakeQ.data?.length ?? 0} 条</Text>}
        >
          {ambiguity.candidates.length > 0 && (
            <Alert
              type="warning"
              showIcon
              icon={<WarningOutlined />}
              style={{ marginBottom: 12 }}
              title={`「${ambiguity.name}」命中 ${ambiguity.candidates.length} 条同名记录`}
              description="请在搜索框里选具体名称，或直接用食物 ID 记录。系统绝不会取平均值或第一条。"
            />
          )}

          {!intakeQ.data?.length ? (
            <Empty description="还没有记录" />
          ) : (
            intakeQ.data.map((it) => (
              <div className="record-row" key={it.id}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <Space size={6} wrap>
                    <Text strong>{it.name}</Text>
                    {it.meal_type && <Tag>{MEAL_LABEL[it.meal_type]}</Tag>}
                    {it.cooking_method && (
                      <Tag color="orange">
                        {it.cooking_method}
                        {it.oil_level ? `·${it.oil_level}` : ""}
                      </Tag>
                    )}
                  </Space>
                  {it.calc_detail && (
                    <details>
                      <summary style={{ fontSize: 11, color: "#9ca3af", cursor: "pointer" }}>
                        计算过程
                      </summary>
                      <Text style={{ fontSize: 11, color: "#6b7280" }}>{it.calc_detail}</Text>
                    </details>
                  )}
                </div>
                <Space>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {it.grams}g
                  </Text>
                  <Text strong style={{ color: "#f59e0b", minWidth: 60, textAlign: "right" }}>
                    {it.kcal} 千卡
                  </Text>
                  <Tooltip title="撤销">
                    <Button
                      size="small"
                      type="text"
                      danger
                      icon={<DeleteOutlined />}
                      onClick={() => delMut.mutate(it.id)}
                    />
                  </Tooltip>
                </Space>
              </div>
            ))
          )}

          {intakeQ.data && intakeQ.data.length > 0 && (
            <>
              <Divider style={{ margin: "10px 0" }} />
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <Text>合计</Text>
                <Text strong style={{ color: "#f59e0b", fontSize: 16 }}>
                  {intakeQ.data.reduce((s, x) => s + x.kcal, 0).toFixed(0)} 千卡
                </Text>
              </div>
            </>
          )}
        </Card>
      </Col>

      {/* 两阶段确认：预览 → 用户确认 → 落库 */}
      <Modal
        open={!!preview}
        title="确认这条记录"
        okText="确认记录"
        cancelText="再改改"
        onCancel={() => setPreview(null)}
        confirmLoading={createMut.isPending}
        onOk={() =>
          preview &&
          createMut.mutate({
            name: preview.name,
            food_id: preview.food_id,
            recipe_id: preview.recipe_id,
            grams: preview.grams,
            meal_type: mealType,
            cooking_method: preview.cooking_method ?? undefined,
            oil_level: preview.oil_level ?? undefined,
            log_date: dateStr,
          })
        }
      >
        {preview && (
          <>
            <Alert
              type="success"
              showIcon
              icon={<CheckCircleOutlined />}
              title={preview.confirm_prompt}
              description={preview.detail}
              style={{ marginBottom: 12 }}
            />
            <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 0 }}>
              这个数值由数据库查得并由后端计算，前端和大模型都不参与算术。
            </Paragraph>
          </>
        )}
      </Modal>

      {/* 红线四：同名异物必须弹候选让用户选。
          绝不能取平均值或第一条 —— 琼脂 26 vs 311，取平均得 179，两边都不对。 */}
      <Modal
        open={ambiguity.open}
        title={
          <Space>
            <WarningOutlined style={{ color: "#f59e0b" }} />
            「{ambiguity.name}」有多个同名记录
          </Space>
        }
        footer={
          <Button onClick={() => setAmbiguity({ open: false, name: "", candidates: [] })}>
            取消
          </Button>
        }
        width={520}
      >
        <Paragraph>
          数据库里有 {ambiguity.candidates.length} 条同名记录，热量差异较大。
          <Text strong>系统不会替你猜，也不会取平均值</Text>，请选择具体是哪一种：
        </Paragraph>
        <Radio.Group
          onChange={(e) => {
            const picked = String(e.target.value ?? "");
            setName(picked.split(" · ")[0]);
            setAmbiguity({ open: false, name: "", candidates: [] });
            msg.info(`已选择「${picked}」，请确认克重后点计算`);
          }}
        >
          <Space orientation="vertical" style={{ marginTop: 8, width: "100%" }}>
            {ambiguity.candidates.map((c, i) => (
              <Radio key={i} value={c}>
                <Text>{c}</Text>
              </Radio>
            ))}
          </Space>
        </Radio.Group>
      </Modal>
    </Row>
  );
}

// ─────────────────────────────────────────────────────────
// 运动
// ─────────────────────────────────────────────────────────

function ExercisePanel({ dateStr, onDone }: { dateStr: string; onDone: () => void }) {
  const { message: msg } = AntApp.useApp();
  const [keyword, setKeyword] = useState("");
  const [name, setName] = useState("");
  const [minutes, setMinutes] = useState<number>(30);

  const dailyQ = useQuery({ queryKey: ["daily-ex"], queryFn: () => api.dailyExercises(60) });
  const suggestQ = useQuery({
    queryKey: ["ex-suggest", keyword],
    queryFn: () => api.searchExercises(keyword, 12),
    enabled: keyword.trim().length >= 1,
  });
  const logsQ = useQuery({
    queryKey: ["exercise-logs", dateStr],
    queryFn: () => api.listExerciseLogs(dateStr),
  });

  const createMut = useMutation({
    mutationFn: api.createExerciseLog,
    onSuccess: (d: LogItem) => {
      msg.success(`已记录：${d.name} ${d.duration_min}分钟 = ${d.kcal} 千卡`);
      setKeyword("");
      setName("");
      onDone();
    },
    onError: (e: Error) => msg.error(e.message),
  });

  const delMut = useMutation({
    mutationFn: (id: number) => api.deleteExerciseLog(id),
    onSuccess: () => {
      msg.success("已撤销");
      onDone();
    },
    onError: (e: Error) => msg.error(e.message),
  });

  /** 先算消耗，确认后再落库 —— 和饮食同一套两阶段逻辑 */
  const submit = async () => {
    if (!name.trim()) return msg.warning("请先选择运动");
    try {
      const r = await api.calcExercise({ name: name.trim(), duration_min: minutes });
      if (r.need_disambiguation) {
        return msg.warning(`「${name}」有多个匹配项，请从下拉里选具体那个`);
      }
      createMut.mutate({ name: name.trim(), duration_min: minutes, log_date: dateStr });
    } catch (e) {
      msg.error((e as Error).message);
    }
  };

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} lg={13}>
        <Card size="small" title="添加运动 / 日常活动">
          <Space orientation="vertical" style={{ width: "100%" }} size={12}>
            <AutoComplete
              value={keyword}
              onChange={setKeyword}
              options={(suggestQ.data ?? []).map((e) => ({
                value: e.name,
                label: (
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                    <span style={{ fontSize: 12 }}>{e.name.slice(0, 46)}</span>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      MET {e.met}
                    </Text>
                  </div>
                ),
              }))}
              style={{ width: "100%" }}
              onSelect={(v) => setName(v)}
              placeholder="输入运动名（如：快走、跑步、拖地）"
              filterOption={(input, opt) =>
                String(opt?.value ?? "").toLowerCase().includes(input.toLowerCase())
              }
            >
              <Input.Search
                enterButton={
                  <Button type="primary" icon={<PlusOutlined />} onClick={submit} loading={createMut.isPending}>
                    记录
                  </Button>
                }
                onSearch={(v) => {
                  setName(v);
                }}
              />
            </AutoComplete>

            <div>
              <Text className="metric-label">时长（分钟）</Text>
              <Space.Compact>
                <InputNumber
                  value={minutes}
                  onChange={(v) => setMinutes(Number(v) || 0)}
                  min={1}
                  max={600}
                  style={{ width: 120 }}
                />
                <Button disabled style={{ pointerEvents: "none" }}>
                  分钟
                </Button>
              </Space.Compact>
            </div>

            <div>
              <Text className="metric-label">
                日常活动（走路、家务、静坐也算消耗）
              </Text>
              <div style={{ marginTop: 8, maxHeight: 200, overflowY: "auto" }}>
                <Space size={6} wrap>
                  {(dailyQ.data ?? []).slice(0, 40).map((e) => (
                    <Tag
                      key={e.id}
                      color={e.met && e.met >= 3 ? "green" : "default"}
                      style={{ cursor: "pointer", marginBottom: 4 }}
                      onClick={() => setName(e.name)}
                    >
                      {e.name.split("：").slice(-2).join("·").slice(0, 22)} · MET {e.met}
                    </Tag>
                  ))}
                </Space>
              </div>
            </div>

            <Alert
              type="warning"
              showIcon
              title={<Text style={{ fontSize: 12 }}>MET 是人群均值，个体差异可达 ±20%。这里给的是估算值，不是精确消耗。</Text>}
            />
          </Space>
        </Card>
      </Col>

      <Col xs={24} lg={11}>
        <Card size="small" title={`${dateStr} 的运动记录`}>
          {!logsQ.data?.length ? (
            <Empty description="还没有运动记录" />
          ) : (
            logsQ.data.map((it) => (
              <div className="record-row" key={it.id}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <Space size={6} wrap>
                    <Text style={{ fontSize: 13 }}>{it.name}</Text>
                    {it.is_daily_activity && <Tag color="blue">日常活动</Tag>}
                  </Space>
                </div>
                <Space>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {it.duration_min} 分钟
                  </Text>
                  <Text strong style={{ color: "#10b981", minWidth: 60, textAlign: "right" }}>
                    {it.kcal} 千卡
                  </Text>
                  <Button
                    size="small"
                    type="text"
                    danger
                    icon={<DeleteOutlined />}
                    onClick={() => delMut.mutate(it.id)}
                  />
                </Space>
              </div>
            ))
          )}
        </Card>
      </Col>
    </Row>
  );
}

// ─────────────────────────────────────────────────────────
// 体重
// ─────────────────────────────────────────────────────────

function WeightPanel({ dateStr, onDone }: { dateStr: string; onDone: () => void }) {
  const { message: msg } = AntApp.useApp();
  const qc = useQueryClient();
  const [kg, setKg] = useState<number | null>(null);
  const listQ = useQuery({ queryKey: ["weight", 90], queryFn: () => api.listWeight(90) });

  // 身高取自个人中心，这里可直接改 —— 之前只能去个人中心改，
  // 用户反馈「身高跟个人中心走，不要默认」。
  const profileQ = useQuery({ queryKey: ["profile"], queryFn: () => api.getProfile() });
  const [height, setHeight] = useState<number | null>(null);
  const shownHeight = height ?? profileQ.data?.height_cm ?? null;

  // BMI 预览用「当前输入的体重 + 当前身高」，与历史表口径一致
  const bmiQ = useQuery(
    {
      queryKey: ["bmi", kg, shownHeight],
      queryFn: () => api.bmi({ weight_kg: kg ?? undefined, height_cm: shownHeight ?? undefined }),
      enabled: !!kg && !!shownHeight,
    },
  );

  // 身高改动会影响全部历史记录的 BMI，所以要连带刷新体重列表
  const heightMut = useMutation({
    mutationFn: (v: number) => api.updateProfile({ height_cm: v }),
    onSuccess: (p) => {
      msg.success(`身高已更新为 ${p.height_cm}cm · 所有历史 BMI 已重算`);
      setHeight(null);
      qc.invalidateQueries({ queryKey: ["weight"] });
      qc.invalidateQueries({ queryKey: ["bmi"] });
      qc.invalidateQueries({ queryKey: ["profile"] });
      qc.invalidateQueries({ queryKey: ["target"] });
      qc.invalidateQueries({ queryKey: ["summary"] });
      onDone();
    },
    onError: (e: Error) => msg.error(e.message),
  });

  const mut = useMutation({
    mutationFn: (v: number) => api.recordWeight(v, dateStr),
    onSuccess: (d) => {
      msg.success(`已记录 ${d.weight_kg}kg · BMI ${d.bmi_value}（${d.bmi_category}）`);
      setKg(null);
      onDone();
    },
    onError: (e: Error) => msg.error(e.message),
  });

  // ── 修改往日体重 ──
  // 为什么不用行内编辑：体重是「一天一次」的唯一键，改错了影响 BMR、
  // 移动平均、热量缺口一整条链。用弹窗把日期和旧值都显示出来，
  // 用户改之前能确认自己改的是哪一天。
  const [editRow, setEditRow] = useState<WeightItem | null>(null);
  const [editKg, setEditKg] = useState<number | null>(null);

  /** 改完体重，BMR / TDEE / 图表 / 推荐全都变了，必须一起刷新 */
  const refreshAfterWeightChange = () => {
    qc.invalidateQueries({ queryKey: ["weight"] });
    qc.invalidateQueries({ queryKey: ["bmi"] });
    qc.invalidateQueries({ queryKey: ["weightTrend"] });
    qc.invalidateQueries({ queryKey: ["profile"] });
    qc.invalidateQueries({ queryKey: ["summary"] });
    qc.invalidateQueries({ queryKey: ["target"] });
    onDone();
  };
  const openEdit = (row: WeightItem) => {
    setEditRow(row);
    setEditKg(row.weight_kg);
  };

  const editMut = useMutation({
    mutationFn: (v: { date: string; kg: number }) =>
      api.updateWeight(v.date, { weight_kg: v.kg }),
    onSuccess: (d) => {
      msg.success(`${d.log_date} 已改为 ${d.weight_kg}kg · BMI 重算为 ${d.bmi_value}`);
      setEditRow(null);
      refreshAfterWeightChange();
    },
    onError: (e: Error) => msg.error(e.message),
  });

  const delMut = useMutation({
    mutationFn: (date: string) => api.deleteWeight(date),
    onSuccess: (_d, date) => {
      msg.success(`已删除 ${date} 的记录`);
      refreshAfterWeightChange();
    },
    onError: (e: Error) => msg.error(e.message),
  });

  return (
    <>
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={10}>
          <Card size="small" title="记录今日体重">
            <Space orientation="vertical" style={{ width: "100%" }} size={12}>
              <Space.Compact style={{ width: "100%" }}>
                <InputNumber
                  value={kg}
                  onChange={setKg}
                  min={25}
                  max={250}
                  step={0.1}
                  style={{ width: "100%" }}
                  placeholder="体重，例如 78.5"
                />
                <Button disabled style={{ width: 44, pointerEvents: "none" }}>
                  kg
                </Button>
              </Space.Compact>

              {/* 身高与个人中心同一份数据。改了会重算所有历史 BMI，
                  所以必须显式保存，不能只改本地状态。 */}
              <Space.Compact style={{ width: "100%" }}>
                <InputNumber
                  value={shownHeight}
                  onChange={setHeight}
                  min={120}
                  max={230}
                  step={0.5}
                  style={{ width: "100%" }}
                  placeholder="身高（与个人中心同步）"
                />
                <Button disabled style={{ width: 44, pointerEvents: "none" }}>
                  cm
                </Button>
              </Space.Compact>
              {height != null && height !== profileQ.data?.height_cm && (
                <Button
                  block
                  type="primary"
                  ghost
                  loading={heightMut.isPending}
                  onClick={() => heightMut.mutate(height)}
                >
                  保存身高并重算历史 BMI
                </Button>
              )}
              {kg && bmiQ.data && (
                <Alert
                  type={bmiQ.data.category === "正常" ? "success" : "warning"}
                  showIcon
                  title={`BMI ${bmiQ.data.bmi} · ${bmiQ.data.category}`}
                />
              )}
              <Button
                type="primary"
                block
                disabled={!kg}
                loading={mut.isPending}
                onClick={() => kg && mut.mutate(kg)}
              >
                保存（一天一次，同日会覆盖）
              </Button>
            </Space>
          </Card>
        </Col>

        <Col xs={24} lg={14}>
          <Card size="small" title="体重历史">
            {!listQ.data?.length ? (
              <Empty description="暂无记录" />
            ) : (
              <Table
                size="small"
                rowKey="log_date"
                pagination={{ pageSize: 8, size: "small" }}
                dataSource={[...listQ.data].reverse()}
                columns={[
                  { title: "日期", dataIndex: "log_date", width: 108, ellipsis: true },
                  {
                    title: "体重",
                    dataIndex: "weight_kg",
                    width: 74,
                    render: (v: number) => `${v}kg`,
                  },
                  { title: "BMI", dataIndex: "bmi_value", render: (v: number | null) => v ?? "-" },
                  {
                    title: "判定",
                    dataIndex: "bmi_category",
                    render: (v: string | null) =>
                      v ? <Tag color={v === "正常" ? "green" : "orange"}>{v}</Tag> : "-",
                  },
                  {
                    title: "操作",
                    key: "ops",
                    width: 92,
                    render: (_: unknown, row: WeightItem) => (
                      <Space size={0}>
                        <Button
                          type="link"
                          size="small"
                          icon={<EditOutlined />}
                          onClick={() => openEdit(row)}
                        >
                          修改
                        </Button>
                        <Popconfirm
                          title="删除这天的体重？"
                          description={`${row.log_date} 的记录会被移除，BMI 与移动平均会重算`}
                          okText="删除"
                          cancelText="取消"
                          okButtonProps={{ danger: true }}
                          onConfirm={() => delMut.mutate(row.log_date)}
                        >
                          <Button
                            type="link"
                            size="small"
                            danger
                            icon={<DeleteOutlined />}
                            loading={delMut.isPending && delMut.variables === row.log_date}
                          />
                        </Popconfirm>
                      </Space>
                    ),
                  },
                ]}
              />
            )}
          </Card>
        </Col>
      </Row>

      <Modal
        open={!!editRow}
        title={`修改 ${editRow?.log_date ?? ""} 的体重`}
        onCancel={() => setEditRow(null)}
        onOk={() => {
          if (editRow && editKg != null) {
            editMut.mutate({ date: editRow.log_date, kg: editKg });
          }
        }}
        okText="保存"
        cancelText="取消"
        confirmLoading={editMut.isPending}
        destroyOnHidden
      >
        {editRow && (
          <Space orientation="vertical" style={{ width: "100%" }} size={12}>
            <Alert
              type="info"
              showIcon
              title={
                <Text style={{ fontSize: 12 }}>
                  原值 <b>{editRow.weight_kg} kg</b>（BMI {editRow.bmi_value}）
                  <br />
                  日期不可改 —— 体重是「一天一次」的唯一键。
                </Text>
              }
            />
            <Space.Compact style={{ width: "100%" }}>
              <InputNumber
                value={editKg}
                onChange={setEditKg}
                min={25}
                max={250}
                step={0.1}
                style={{ width: "100%" }}
                placeholder="新的体重"
              />
              <Button disabled style={{ width: 44, pointerEvents: "none" }}>
                kg
              </Button>
            </Space.Compact>
            {editKg != null && editKg !== editRow.weight_kg && (
              <Text type="secondary" style={{ fontSize: 11 }}>
                BMI 与判定会按新体重自动重算，7 日移动平均也会随之更新。
              </Text>
            )}
          </Space>
        )}
      </Modal>
    </>
  );
}
