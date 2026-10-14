import { useMemo, useState } from "react";
import {
  Button,
  Card,
  Col,
  Progress,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  Alert,
  Empty,
} from "antd";
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  FireOutlined,
  HeartOutlined,
  InfoCircleOutlined,
  SwapOutlined,
} from "@ant-design/icons";
import { useQueries } from "@tanstack/react-query";
import ReactECharts from "echarts-for-react";

import { api } from "../api/client";
import type { RecommendItem } from "../api/types";

const { Paragraph, Text } = Typography;

/**
 * 看板页。
 *
 * 三个刻意做出来的设计：
 * 1. BMR 与 TDEE 同时显示 —— 用户最大的认知误区就是把基础代谢当日消耗
 * 2. 体重折线必须叠加 7 日移动平均 —— 日间 ±1.5kg 波动属正常，
 *    只画原始值用户看到噪音就放弃了
 * 3. 推荐区展示实际执行的 SQL —— 证明这是结构化查询而非向量检索
 */
/** 推荐每批条数。「换一批」按 offset 翻页，可完整遍历 29 条不重复 */
const REC_BATCH = 8;
/** 图表天数。7 天太短看不出趋势，14 天能看出「周末 vs 工作日」的差异。 */
const CHART_DAYS = 14;

export default function DashboardPage() {
  // 推荐翻页：offset 决定看第几批，到末尾后自动回到第一批
  const [recOffset, setRecOffset] = useState(0);

  const [summaryQ, rangeQ, trendQ, targetQ, bmiQ, recQ] = useQueries({
    queries: [
      { queryKey: ["summary"], queryFn: () => api.summary() },
      { queryKey: ["range", CHART_DAYS], queryFn: () => api.summaryRange(CHART_DAYS) },
      { queryKey: ["trend", 30], queryFn: () => api.weightTrend(30) },
      { queryKey: ["target"], queryFn: () => api.energyTarget() },
      { queryKey: ["bmi"], queryFn: () => api.bmi() },
      {
        queryKey: ["recommend", recOffset],
        queryFn: () =>
          api.recommend({ max_kcal: 80, min_protein: 5, limit: REC_BATCH, offset: recOffset }),
      },
    ],
  });

  const summary = summaryQ.data;
  const target = targetQ.data;
  const bmi = bmiQ.data;
  const trend = trendQ.data;
  const range = rangeQ.data;
  const rec = recQ.data;

  /** 摄入进度条：目标减去已摄入 */
  const intakePercent = useMemo(() => {
    if (!summary?.target_kcal) return 0;
    return Math.min(100, (summary.intake_kcal / summary.target_kcal) * 100);
  }, [summary]);

  /** 体重折线：原始值 + 7 日移动平均 */
  const weightOption = useMemo(() => {
    if (!trend?.points?.length) return null;
    const dates = trend.points.map((p) => p.log_date.slice(5));
    return {
      tooltip: { trigger: "axis" },
      legend: { data: ["体重", "7日移动平均"], top: 0, icon: "roundRect" },
      grid: { left: 48, right: 20, top: 36, bottom: 32 },
      xAxis: { type: "category", data: dates, boundaryGap: false },
      yAxis: {
        type: "value",
        scale: true,
        name: "kg",
        axisLabel: { formatter: (v: number) => v.toFixed(1) },
      },
      series: [
        {
          name: "体重",
          type: "line",
          data: trend.points.map((p) => p.weight_kg),
          showSymbol: false,
          lineStyle: { width: 1.5, color: "#d1d5db" },
          itemStyle: { color: "#d1d5db" },
        },
        {
          name: "7日移动平均",
          type: "line",
          data: trend.points.map((p) => p.moving_avg ?? null),
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 3, color: "#10b981" },
          itemStyle: { color: "#10b981" },
          areaStyle: {
            color: {
              type: "linear",
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: "rgba(16,185,129,0.22)" },
                { offset: 1, color: "rgba(16,185,129,0.02)" },
              ],
            },
          },
        },
      ],
    };
  }, [trend]);

  /** 7 日收支柱状图 */
  const rangeOption = useMemo(() => {
    if (!range?.points?.length) return null;
    return {
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      legend: { data: ["摄入", "运动消耗"], top: 0, icon: "roundRect" },
      grid: { left: 52, right: 20, top: 36, bottom: 32 },
      xAxis: { type: "category", data: range.points.map((p) => p.date.slice(5)) },
      yAxis: { type: "value", name: "千卡", axisLabel: { formatter: (v: number) => v } },
      series: [
        {
          name: "摄入",
          type: "bar",
          data: range.points.map((p) => p.intake_kcal),
          itemStyle: { color: "#f59e0b", borderRadius: [4, 4, 0, 0] },
          barMaxWidth: 26,
        },
        {
          name: "运动消耗",
          type: "bar",
          data: range.points.map((p) => p.exercise_kcal),
          itemStyle: { color: "#10b981", borderRadius: [4, 4, 0, 0] },
          barMaxWidth: 26,
        },
      ],
    };
  }, [range]);

  const recColumns = [
    {
      title: "食物",
      dataIndex: "name",
      render: (v: string, r: RecommendItem) => (
        <div>
          <Text strong>{v}</Text>
          {r.category && (
            <Tag style={{ marginLeft: 8 }} color="green">
              {r.category}
            </Tag>
          )}
        </div>
      ),
    },
    {
      title: "热量",
      dataIndex: "kcal_per_100g",
      render: (v: number) => <Text>{v} 千卡/100g</Text>,
    },
    {
      title: "蛋白质",
      dataIndex: "protein_per_100g",
      render: (v: number | null) => <Text>{v ?? "-"} g</Text>,
    },
    {
      title: "蛋白密度",
      dataIndex: "protein_density",
      render: (v: number | null) =>
        v == null ? (
          "-"
        ) : (
          <Tooltip title="每 100 千卡含多少克蛋白质，越高越扛饿">
            <Text strong style={{ color: "#059669" }}>
              {v.toFixed(1)} g/100kcal
            </Text>
          </Tooltip>
        ),
    },
  ];

  return (
    <div className="page-stack">
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <Card className="stat-card">
            <Statistic
              title="今日摄入"
              value={Math.round(summary?.intake_kcal ?? 0)}
              suffix="千卡"
              styles={{ content: { color: "#f59e0b" } }}
            />
            <Progress
              percent={intakePercent}
              strokeColor="#f59e0b"
              size="small"
              format={() => `目标 ${summary?.target_kcal ?? "-"}`}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              {summary?.status}
            </Text>
          </Card>
        </Col>

        <Col xs={24} sm={12} lg={6}>
          <Card className="stat-card">
            <Statistic
              title={
                <span>
                  今日消耗
                  <Tooltip title="基础代谢 + 活动系数。运动消耗已额外计入下面的「运动」">
                    <InfoCircleOutlined style={{ marginLeft: 4, color: "#9ca3af" }} />
                  </Tooltip>
                </span>
              }
              value={Math.round((summary?.tdee_value ?? 0) + (summary?.exercise_kcal ?? 0))}
              suffix="千卡"
              styles={{ content: { color: "#10b981" } }}
            />
            <div style={{ marginTop: 6, fontSize: 12 }}>
              <div className="metric-label">
                基础代谢 BMR <b style={{ color: "#6b7280" }}>{summary?.bmr?.toFixed(0)}</b>
              </div>
              <div className="metric-label">
                日常总消耗 TDEE{" "}
                <b style={{ color: "#6b7280" }}>{summary?.tdee_value?.toFixed(0)}</b>
              </div>
              <div className="metric-label">
                额外运动 <b style={{ color: "#10b981" }}>{summary?.exercise_kcal?.toFixed(0)}</b>
              </div>
            </div>
          </Card>
        </Col>

        <Col xs={24} sm={12} lg={6}>
          <Card className="stat-card">
            <Statistic
              title="热量缺口"
              value={Math.round(summary?.balance ?? 0)}
              suffix="千卡"
              prefix={
                (summary?.balance ?? 0) >= 0 ? (
                  <ArrowUpOutlined style={{ color: "#ef4444" }} />
                ) : (
                  <ArrowDownOutlined style={{ color: "#10b981" }} />
                )
              }
              styles={{ content: { color: (summary?.balance ?? 0) >= 0 ? "#ef4444" : "#10b981" } }}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              {summary?.concept_note}
            </Text>
          </Card>
        </Col>

        <Col xs={24} sm={12} lg={6}>
          <Card className="stat-card">
            <Statistic
              title={
                <span>
                  BMI <HeartOutlined style={{ color: "#ef4444" }} />
                </span>
              }
              value={bmi?.bmi ?? 0}
              precision={1}
              suffix={
                <Tag color={bmi?.category === "正常" ? "green" : "orange"} style={{ marginLeft: 6 }}>
                  {bmi?.category}
                </Tag>
              }
            />
            {bmi?.goal_plan && (
              <div style={{ marginTop: 6, fontSize: 12 }}>
                <div className="metric-label">
                  WHO 标准 <b style={{ color: "#6b7280" }}>{bmi.who_category}</b>
                </div>
                <div className="metric-label">
                  建议每日缺口 <b style={{ color: "#6b7280" }}>{bmi.goal_plan.daily_deficit_kcal}</b> 千卡
                </div>
              </div>
            )}
          </Card>
        </Col>
      </Row>

      {(summary?.target_warnings?.length || target?.warnings?.length) && (
        <Alert
          type="warning"
          showIcon
          title="热量目标提示"
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {(summary?.target_warnings ?? target?.warnings ?? []).map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          }
        />
      )}

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <Card title="体重趋势（含 7 日移动平均）" style={{ minWidth: 0 }}>
            {weightOption ? (
              <ReactECharts option={weightOption} style={{ height: 320 }} notMerge />
            ) : (
              <Empty description="暂无体重记录，去「记录」页添加第一条" />
            )}
            {trend?.message && (
              <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
                <FireOutlined style={{ color: "#f59e0b" }} /> {trend.message}
              </Paragraph>
            )}
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card title={`近 ${CHART_DAYS} 日摄入 / 运动消耗`} style={{ minWidth: 0 }}>
            {rangeOption ? (
              <ReactECharts option={rangeOption} style={{ height: 320 }} notMerge />
            ) : (
              <Empty description="暂无数据" />
            )}
          </Card>
        </Col>
      </Row>

      <Card
        title="低热量高蛋白推荐"
        extra={
          <Space>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {rec ? `第 ${recOffset + 1}-${recOffset + rec.items.length} / ${rec.total} 条` : ""}
            </Text>
            <Tooltip title="服务端用 SQL 硬过滤 + 蛋白密度排序，不是向量检索">
              <Tag color="green">结构化查询</Tag>
            </Tooltip>
            <Button
              size="small"
              icon={<SwapOutlined spin={recQ.isFetching} />}
              disabled={recQ.isFetching}
              onClick={() =>
                // 到末尾就绕回第一批，让「换一批」永远有反馈
                setRecOffset((o) =>
                  rec?.has_more ? o + REC_BATCH : 0,
                )
              }
            >
              换一批
            </Button>
          </Space>
        }
      >
        <Paragraph type="secondary" style={{ fontSize: 12 }}>
          筛选条件：热量 ≤ 80 千卡/100g 且蛋白质 ≥ 5g，按蛋白密度降序。低卡食物如果蛋白不高会很快饿，
          两个条件一起用才有意义。
        </Paragraph>
        <Table
          size="small"
          rowKey="name"
          columns={recColumns}
          dataSource={rec?.items ?? []}
          pagination={false}
          locale={{ emptyText: "加载中…" }}
        />
        {rec?.sql && (
          <details style={{ marginTop: 12 }}>
            <summary style={{ cursor: "pointer", fontSize: 12, color: "#6b7280" }}>
              查看本次实际执行的 SQL
            </summary>
            <div className="sql-box" style={{ marginTop: 8 }}>
              {rec.sql}
            </div>
          </details>
        )}
      </Card>
    </div>
  );
}
