import { useEffect, useState } from "react";
import { Layout, Tag, Spin, Alert } from "antd";
import {
  DashboardOutlined,
  EditOutlined,
  RobotOutlined,
  SettingOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";

import DashboardPage from "./pages/DashboardPage";
import ProfilePage from "./pages/ProfilePage";
import RecordPage from "./pages/RecordPage";
import ChatPage from "./pages/ChatPage";
import { api } from "./api/client";

const { Header, Content } = Layout;

interface HealthInfo {
  status: string;
  backend: string;
  llm_configured: boolean;
  food_count: number;
  exercise_count: number;
  recipe_count: number;
}

/** 顶部导航。必须始终完整可见，不能被折叠。 */
const NAV_ITEMS = [
  { key: "/", label: "看板", icon: <DashboardOutlined /> },
  { key: "/record", label: "记录", icon: <EditOutlined /> },
  { key: "/chat", label: "智能问答", icon: <RobotOutlined /> },
  { key: "/profile", label: "个人中心", icon: <SettingOutlined /> },
];

export default function App() {
  const loc = useLocation();
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .health()
      .then((d) => alive && setHealth(d))
      .catch((e: Error) => alive && setHealthError(e.message));
    return () => {
      alive = false;
    };
  }, []);

  const selected = loc.pathname.startsWith("/record")
    ? "/record"
    : loc.pathname.startsWith("/chat")
      ? "/chat"
      : loc.pathname.startsWith("/profile")
        ? "/profile"
        : "/";

  return (
    <Layout className="app-shell">
      <Header className="app-header">
        <div className="app-logo">
          <span className="app-logo-badge">🔥</span>
          <span className="app-logo-text">热量记录 · 智能减肥助手</span>
        </div>
        {/*
          不用 antd 的 horizontal Menu。
          它的横向模式在窗口不够宽时会把尾部项收进「…」，而 antd v6 已经
          移除了 overflowMode 控制这个行为（传了会变成非法 DOM 属性），
          只能用 CSS 硬改内部类名 —— 实测在真实浏览器里不可靠：
          无头模式看不到问题，有头模式下「个人中心」会整个消失。
          导航只有 4 项且必须始终可见，这里直接自己渲染，不跟折叠逻辑较劲。
        */}
        <nav className="app-nav">
          {NAV_ITEMS.map((it) => (
            <Link
              key={it.key}
              to={it.key}
              className={`app-nav-item${selected === it.key ? " app-nav-item-active" : ""}`}
            >
              {it.icon}
              <span>{it.label}</span>
            </Link>
          ))}
        </nav>
        <div>
          {healthError ? (
            <Tag color="error" icon={<ThunderboltOutlined />}>
              后端未连接
            </Tag>
          ) : health ? (
            <Tag color="success">
              {health.food_count} 食物 · {health.exercise_count} 运动 ·{" "}
              {health.recipe_count} 菜谱
            </Tag>
          ) : (
            <Spin size="small" />
          )}
        </div>
      </Header>

      <Content className="app-body">
        {healthError && (
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 16 }}
            title="无法连接后端服务"
            description={
              <span>
                请先在项目根目录启动：{" "}
                <code>.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000</code>
                <br />
                错误详情：{healthError}
              </span>
            }
          />
        )}

        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/record" element={<RecordPage />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/profile" element={<ProfilePage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Content>
    </Layout>
  );
}
