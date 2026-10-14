import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Divider,
  Form,
  InputNumber,
  Radio,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Tag,
  Tooltip,
  Typography,
  App as AntApp,
} from "antd";
import {
  CheckCircleOutlined,
  InfoCircleOutlined,
  SaveOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { ProfileResponse } from "../api/types";

const { Paragraph, Text } = Typography;

/** 活动系数的说明 —— 这是个容易填错的字段，必须给出判断标准 */
const ACTIVITY_LEVELS = [
  { value: 1.2, label: "久坐", desc: "几乎不运动，办公室工作" },
  { value: 1.375, label: "轻度活动", desc: "每周 1-3 天轻运动（推荐默认值）" },
  { value: 1.55, label: "中度活动", desc: "每周 3-5 天中等运动" },
  { value: 1.725, label: "高强度", desc: "每周 6-7 天高强度运动" },
  { value: 1.9, label: "运动员", desc: "体力劳动或每日双练" },
];

const GOALS = [
  { value: "lose", label: "减脂", desc: "制造热量缺口" },
  { value: "maintain", label: "维持", desc: "保持当前体重" },
  { value: "gain", label: "增重", desc: "增加肌肉" },
];

/**
 * 个人管理中心。
 *
 * 设计要点：BMR / TDEE 的算式必须完整展示。
 * 用户看到「1740.8 千卡」如果不知道它是怎么来的，就无法判断该不该相信。
 * 展示算式 + 体重取自哪一天，是这个页面存在的核心理由 ——
 * 不是为了好看，是为了让数字可验证。
 *
 * 未来接入登录时：这里的字段直接对应 user 表的 profile 字段，
 * 页面本身不需要改，只需把 getProfile/updateProfile 换成按 user_id 取数。
 */
export default function ProfilePage() {
  const { message: msg } = AntApp.useApp();
  const qc = useQueryClient();
  const [form] = Form.useForm();

  const profileQ = useQuery({ queryKey: ["profile"], queryFn: () => api.getProfile() });
  const bmiQ = useQuery({ queryKey: ["bmi"], queryFn: () => api.bmi() });
  const [activityLevel, setActivityLevel] = useState<number | null>(null);

  // 表单初值随接口返回填充
  useEffect(() => {
    if (profileQ.data) {
      const p = profileQ.data;
      form.setFieldsValue({
        gender: p.gender,
        age: p.age,
        height_cm: p.height_cm,
        weight_kg: p.weight_kg,
        activity_level: p.activity_level,
        goal: p.goal,
        target_weight_kg: p.target_weight_kg,
      });
      setActivityLevel(p.activity_level);
    }
  }, [profileQ.data, form]);

  const saveMut = useMutation({
    mutationFn: (payload: Record<string, unknown>) => api.updateProfile(payload),
    onSuccess: (d: ProfileResponse) => {
      msg.success(
        `已保存 · 基础代谢 ${d.bmr_sources?.bmr?.toFixed(0)} 千卡，每日目标 ${d.daily_calorie_target} 千卡`,
      );
      // BMR 变了，看板、对话、推荐全部要跟着重算
      qc.invalidateQueries({ queryKey: ["profile"] });
      qc.invalidateQueries({ queryKey: ["summary"] });
      qc.invalidateQueries({ queryKey: ["target"] });
      qc.invalidateQueries({ queryKey: ["bmi"] });
    },
    onError: (e: Error) => msg.error(e.message),
  });

  const onSubmit = async () => {
    try {
      const v = await form.validateFields();
      saveMut.mutate({
        ...v,
        // 目标体重允许为空，不要传 undefined 让后端当成"没改"
        ...(v.target_weight_kg == null ? { target_weight_kg: null } : {}),
      });
    } catch {
      msg.warning("请检查表单填写");
    }
  };

  const p = profileQ.data;
  const b = p?.bmr_sources;

  return (
    <div className="page-stack">
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <Card title="基本信息" extra={<Tag color="blue">身高年龄决定基础代谢</Tag>}>
            <Spin spinning={profileQ.isLoading}>
            <Form form={form} layout="vertical">
              <Row gutter={16}>
                <Col span={8}>
                  <Form.Item name="gender" label="性别" rules={[{ required: true }]}>
                    <Radio.Group
                      optionType="button"
                      buttonStyle="solid"
                      options={[
                        { value: "male", label: "男" },
                        { value: "female", label: "女" },
                      ]}
                    />
                  </Form.Item>
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    Mifflin 公式男女常数不同（+5 / −161），必须填写
                  </Text>
                </Col>
                <Col span={8}>
                  <Form.Item
                    name="age"
                    label="年龄"
                    rules={[{ required: true, message: "请输入年龄" }]}
                  >
                    <InputNumber min={10} max={100} style={{ width: "100%" }} suffix="岁" />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item
                    name="height_cm"
                    label="身高"
                    rules={[{ required: true, message: "请输入身高" }]}
                  >
                    <InputNumber min={120} max={230} step={0.5} style={{ width: "100%" }} suffix="cm" />
                  </Form.Item>
                </Col>
              </Row>

              <Row gutter={16}>
                <Col span={8}>
                  <Form.Item
                    name="weight_kg"
                    label={
                      <Tooltip title="这里填的是当前体重。保存后会写入今天的体重记录，基础代谢按此计算。">
                        <span>
                          当前体重 <InfoCircleOutlined style={{ color: "#9ca3af" }} />
                        </span>
                      </Tooltip>
                    }
                    rules={[{ required: true, message: "请输入体重" }]}
                  >
                    <InputNumber min={25} max={250} step={0.1} style={{ width: "100%" }} suffix="kg" />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item name="target_weight_kg" label="目标体重（选填）">
                    <InputNumber min={25} max={250} step={0.1} style={{ width: "100%" }} suffix="kg" />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item name="goal" label="目标">
                    <Select
                      options={GOALS.map((g) => ({
                        value: g.value,
                        label: (
                          <Tooltip title={g.desc}>
                            <span>{g.label}</span>
                          </Tooltip>
                        ),
                      }))}
                    />
                  </Form.Item>
                </Col>
              </Row>

              <Form.Item
                name="activity_level"
                label="活动系数"
                tooltip="决定 TDEE。这个值填高了会导致目标热量偏高，实际摄入不够，减重会停滞。"
              >
                <Select
                  onChange={(v) => setActivityLevel(v)}
                  options={ACTIVITY_LEVELS.map((a) => ({
                    value: a.value,
                    label: `×${a.value} · ${a.label}`,
                  }))}
                />
              </Form.Item>
              {activityLevel && (
                <Alert
                  type="info"
                  style={{ marginBottom: 16 }}
                  title={
                    <Text style={{ fontSize: 12 }}>
                      {ACTIVITY_LEVELS.find((a) => a.value === activityLevel)?.label}：
                      {ACTIVITY_LEVELS.find((a) => a.value === activityLevel)?.desc}
                    </Text>
                  }
                />
              )}

              <Button
                type="primary"
                icon={<SaveOutlined />}
                loading={saveMut.isPending}
                onClick={onSubmit}
              >
                保存档案
              </Button>
            </Form>
            </Spin>
          </Card>
        </Col>

        <Col xs={24} lg={10}>
          <Card
            title="基础代谢怎么算的"
            extra={
              b ? (
                <Tag color={b.weight_source === "latest_log" ? "green" : "orange"}>
                  {b.weight_source === "latest_log" ? "体重取自记录" : "体重取自档案"}
                </Tag>
              ) : undefined
            }
          >
            {b ? (
              <>
                <Statistic
                  title="基础代谢 BMR"
                  value={b.bmr}
                  precision={1}
                  suffix="千卡"
                  styles={{ content: { color: "#10b981" } }}
                />
                <Divider style={{ margin: "12px 0" }} />

                <Descriptions size="small" column={1} colon={false}>
                  <Descriptions.Item label="计算公式">{b.formula}</Descriptions.Item>
                  <Descriptions.Item label="体重">
                    <Space size={4}>
                      <Text strong>{b.weight_kg} kg</Text>
                      {b.weight_date ? (
                        <Tag color="green">{b.weight_date} 记录</Tag>
                      ) : (
                        <Tag color="orange">档案设定值</Tag>
                      )}
                    </Space>
                  </Descriptions.Item>
                  <Descriptions.Item label="身高">{b.height_cm} cm</Descriptions.Item>
                  <Descriptions.Item label="年龄">{b.age} 岁</Descriptions.Item>
                </Descriptions>

                <div
                  className="sql-box"
                  style={{ marginTop: 12, background: "#ecfdf5", color: "#065f46" }}
                >
                  {b.expression}
                </div>

                <div style={{ marginTop: 14 }}>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <Text type="secondary">BMR × 活动系数 {b.activity_level}</Text>
                    <Text type="secondary">= TDEE</Text>
                  </div>
                  <Statistic
                    value={b.tdee}
                    precision={1}
                    suffix="千卡"
                    styles={{ content: { color: "#0d9488" } }}
                  />
                  {p?.daily_calorie_target && (
                    <Alert
                      type="success"
                      icon={<CheckCircleOutlined />}
                      style={{ marginTop: 8 }}
                      title={
                        <Text style={{ fontSize: 13 }}>
                          每日热量目标 <b>{p.daily_calorie_target}</b> 千卡
                          （目标：{GOALS.find((g) => g.value === p.goal)?.label}）
                        </Text>
                      }
                      description={
                        <Text style={{ fontSize: 11 }}>
                          服务端按「不低于 BMR、不低于性别安全下限、赤字不超过 TDEE 25%」三条约束算出，
                          不会给出危险目标。
                        </Text>
                      }
                    />
                  )}
                </div>

                <Alert
                  type="warning"
                  showIcon
                  style={{ marginTop: 14 }}
                  title={<Text style={{ fontSize: 11 }}>{b.accuracy_note}</Text>}
                />
              </>
            ) : (
              <Text type="secondary">加载中…</Text>
            )}
          </Card>

          {bmiQ.data && (
            <Card title="当前身体指标" style={{ marginTop: 16 }}>
              <Row gutter={16}>
                <Col span={8}>
                  <Statistic
                    title="BMI"
                    value={bmiQ.data.bmi}
                    precision={1}
                    suffix={
                      <Tag color={bmiQ.data.category === "正常" ? "green" : "orange"}>
                        {bmiQ.data.category}
                      </Tag>
                    }
                  />
                </Col>
                <Col span={16}>
                  <div style={{ fontSize: 12, color: "#6b7280", lineHeight: 1.9 }}>
                    <div>WHO 标准：{bmiQ.data.who_category}</div>
                    <div>算式：{bmiQ.data.formula}</div>
                  </div>
                </Col>
              </Row>
              {bmiQ.data.goal_plan && (
                <>
                  <Divider style={{ margin: "10px 0" }} />
                  <Paragraph style={{ fontSize: 12, margin: 0 }}>
                    按每周 {bmiQ.data.goal_plan.weekly_rate_kg}kg 的速度减到目标体重，
                    每日需缺口 <b>{bmiQ.data.goal_plan.daily_deficit_kcal}</b> 千卡。
                    {!bmiQ.data.goal_plan.match && (
                      <Tag color="orange" style={{ marginLeft: 8 }}>
                        与当前实际缺口不符
                      </Tag>
                    )}
                  </Paragraph>
                </>
              )}
            </Card>
          )}
        </Col>
      </Row>

      <Card title="这个页面未来会怎么变">
        <Paragraph style={{ fontSize: 12, marginBottom: 0 }}>
          当前是单用户演示模式，档案存在全库唯一的一行 <Text code>profile</Text> 表里。
          接入登录后，这张表会加 <Text code>user_id</Text> 并成为多行，
          本页面只需把 <Text code>getProfile / updateProfile</Text> 换成按当前用户取数，
          表单与计算逻辑都不用改 —— 因为 BMR 的计算本来就与「谁在算」无关。
        </Paragraph>
      </Card>
    </div>
  );
}
