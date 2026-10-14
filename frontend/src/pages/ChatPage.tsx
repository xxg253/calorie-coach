import { useEffect, useRef, useState } from "react";
import {
  Button,
  Card,
  Input,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
  App as AntApp,
  Empty,
} from "antd";
import { ClearOutlined, InfoCircleOutlined, SendOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";

import { api, chatStream } from "../api/client";
import type { ChatCitation } from "../api/types";
import { ErrorBoundary } from "../components/ErrorBoundary";

const { Text } = Typography;

interface Msg {
  role: "user" | "assistant";
  content: string;
  citations?: ChatCitation[];
  /** 模型实际用到的数据库返回值，用于「数据可信」面板 */
  toolData?: Array<Record<string, unknown>>;
  guardWarning?: string | null;
  clarify?: {
    message: string;
    options?: Array<{
      value?: string;
      label?: string;
      id?: number;
      name?: string;
      kcal_per_100g?: number;
    }>;
  } | null;
  intentLabel?: string;
  streaming?: boolean;
}

/**
 * 会话持久化。
 *
 * 之前 msgs 只在 useState 里，切到别的页面组件卸载，对话就没了。
 * 现在双保险：
 * 1. 每次变化把 sessionId + msgs 存进 localStorage（切页/刷新都能恢复）
 * 2. 挂载时若本地为空，从后端拉最近一次会话（换浏览器也能续上）
 */
const STORAGE_KEY = "calorie-chat-state";

interface SavedState {
  sessionId?: string;
  msgs: Msg[];
}

function saveState(sessionId: string | undefined, msgs: Msg[]) {
  try {
    // 不存 streaming 中的临时态 —— 它是半截内容，恢复出来会很怪
    const clean = msgs.filter((m) => !m.streaming);
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ sessionId, msgs: clean }));
  } catch {
    // 隐私模式下 localStorage 可能不可用，忽略即可（退回内存态）
  }
}

function loadState(): SavedState | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as SavedState;
    return Array.isArray(parsed.msgs) ? parsed : null;
  } catch {
    return null;
  }
}

const PRESETS = [
  "200克米饭的热量是多少",
  "西红柿炒鸡蛋多少热量",
  "快走30分钟消耗多少",
  "我175cm 78kg BMI是多少",
  "推荐几个低热量高蛋白的食物",
  "一杯珍珠奶茶热量",
];

// 渲染错误边界：任何渲染异常降级为提示，不整页白屏。
// 详见 components/ErrorBoundary.tsx 注释。
function ChatPageInner() {
  const { message: msg } = AntApp.useApp();
  const [input, setInput] = useState("");
  // 初值直接取本地缓存：切页回来时组件重新挂载，这里就已经有内容了
  const [msgs, setMsgs] = useState<Msg[]>(() => loadState()?.msgs ?? []);
  const [loading, setLoading] = useState(false);
  // 从后端拉历史期间显示提示，避免误以为对话丢了
  const [restoring, setRestoring] = useState(true);
  const sessionRef = useRef<string | undefined>(undefined);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const designQ = useQuery({ queryKey: ["chat-design"], queryFn: () => httpGetJson("/api/chat/design") });

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [msgs]);

  // 变化时落盘 —— 切页/刷新后能恢复
  useEffect(() => {
    saveState(sessionRef.current, msgs);
  }, [msgs]);

  // 本地为空时从后端拉最近一次会话（换浏览器、清了缓存也能续上）
  useEffect(() => {
    if (loadState()?.msgs?.length) return;
    let alive = true;
    api
      .chatSessions(1)
      .then((list) => {
        if (!alive || !list.length) return;
        const latest = list[0];
        sessionRef.current = latest.thread_id;
        return api.chatMessages(latest.thread_id).then((h) => {
          if (!alive || !h.messages.length) return;
          setMsgs(
            h.messages.map((m) => ({
              role: m.role,
              content: m.content,
              citations: m.citations,
              intentLabel: m.intent ?? undefined,
            })),
          );
        });
      })
      .catch(() => {
        // 拉不到历史不影响新对话，静默降级
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    const t = setTimeout(() => setRestoring(false), 2500);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => () => abortRef.current?.abort(), []);

  const send = async (text: string) => {
    const q = text.trim();
    if (!q || loading) return;

    setInput("");
    setMsgs((m) => [...m, { role: "user", content: q }]);

    // 立刻占位一条 assistant，边流边追加内容
    const idx = Date.now();
    setMsgs((m) => [...m, { role: "assistant", content: "", streaming: true }]);
    setLoading(true);

    const ac = new AbortController();
    abortRef.current = ac;

    const patch = (fn: (m: Msg) => Msg) => {
      setMsgs((all) => all.map((m, i) => (i === all.length - 1 ? fn(m) : m)));
    };

    try {
      await chatStream(q, sessionRef.current, (e) => {
        if (e.type === "intent") {
          sessionRef.current = e.data.session_id ?? sessionRef.current;
          patch((m) => ({ ...m, intentLabel: e.data.label }));
        } else if (e.type === "tool") {
          patch((m) => ({ ...m, toolData: e.data.results ?? [] }));
        } else if (e.type === "clarify") {
          patch((m) => ({
            ...m,
            content: m.content || e.data.message,
            clarify: { message: e.data.message, options: e.data.options },
          }));
        } else if (e.type === "token") {
          // 防御：后端异常时可能重复推送，内容会翻倍并撑爆页面。
          // 单条消息上限 20 万字符足够装下任何正常回答。
          patch((m) => {
            const next = m.content + e.data.content;
            return {
              ...m,
              content:
                next.length > 200000 ? next.slice(0, 200000) : next,
            };
          });
        } else if (e.type === "done") {
          sessionRef.current = e.data.session_id ?? sessionRef.current;
          patch((m) => ({
            ...m,
            // 流式已经累积了内容；done.answer 只在流式没推过内容时兜底。
            // 无条件覆盖会把已显示的内容换成另一份（后端重复推送时），
            // 造成内容翻倍。
            content:
              m.content && m.content.trim() ? m.content : e.data.answer ?? m.content,
            citations: e.data.citations,
            guardWarning: e.data.guard_warning,
            streaming: false,
          }));
        } else if (e.type === "error") {
          patch((m) => ({ ...m, content: e.data.message, streaming: false }));
        }
      }, ac.signal);
    } catch (e) {
      const err = e as Error;
      if (err.name !== "AbortError") {
        patch((m) => ({ ...m, content: `请求失败：${err.message}`, streaming: false }));
        msg.error(err.message);
      }
    } finally {
      patch((m) => ({ ...m, streaming: false }));
      setLoading(false);
      abortRef.current = null;
    }
    void idx;
  };

  const clear = () => {
    abortRef.current?.abort();
    setMsgs([]);
    sessionRef.current = undefined;
    // 只清界面与 session 引用，不删后端历史 —— 那是可回看的记录
    saveState(undefined, []);
  };

  return (
    <div className="chat-wrap">
      <Card size="small" style={{ marginBottom: 12 }} styles={{ body: { padding: "10px 14px" } }}>
        <Space size={8} wrap>
          <Text style={{ fontSize: 12, color: "#6b7280" }}>
            <ThunderboltOutlined /> 试试：
          </Text>
          {PRESETS.map((p) => (
            <Tag
              key={p}
              color="green"
              style={{ cursor: "pointer" }}
              onClick={() => !loading && send(p)}
            >
              {p}
            </Tag>
          ))}
          <Tooltip title="清空对话（会重置多轮上下文）">
            <Button size="small" icon={<ClearOutlined />} onClick={clear}>
              清空
            </Button>
          </Tooltip>
        </Space>
        {designQ.data && (
          <Text
            style={{ fontSize: 11, color: "#9ca3af", display: "block", marginTop: 6 }}
          >
            <InfoCircleOutlined /> 回答里的每个数字都由数据库查询后填充，大模型只负责组织语言，
            不做算术 —— 这是本系统的核心约束。
          </Text>
        )}
      </Card>

      <div className="chat-scroll" ref={scrollRef}>
        {!msgs.length && restoring && (
          <div style={{ textAlign: "center", padding: 60 }}>
            <Spin description="正在恢复上次的对话…" />
          </div>
        )}

        {!msgs.length && !restoring && (
          <Empty
            style={{ marginTop: 60 }}
            description={
              <Space orientation="vertical">
                <Text type="secondary">问点什么，比如「200克米饭的热量是多少」</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  涉及菜谱时会追问烹饪方式和用油量 —— 这是刻意设计的，用油量能让热量差出 40%
                </Text>
              </Space>
            }
          />
        )}

        {msgs.map((m, i) =>
          m.role === "user" ? (
            <div key={i} style={{ display: "flex", marginBottom: 12 }}>
              <div className="chat-bubble user">{m.content}</div>
            </div>
          ) : (
            <div key={i} style={{ marginBottom: 16 }}>
              {m.intentLabel && (
                <Tag color="blue" style={{ marginBottom: 4 }}>
                  意图：{m.intentLabel}
                </Tag>
              )}
              <div
                className={`chat-bubble assistant${m.streaming && !m.content ? " cursor-blink" : ""}`}
              >
                {m.content || (m.streaming ? "" : "（无内容）")}
              </div>

              {/* 追问选项 —— 点一下就能回答，不用打字 */}
              {m.clarify?.options?.length ? (
                <div style={{ marginTop: 8 }}>
                  <Text style={{ fontSize: 12, color: "#6b7280" }}>
                    点击选项直接回答：
                  </Text>
                  <div className="msg-toolbar">
                    {m.clarify.options.map((o, i) => {
                      // 后端历史上用过两种字段名（id/name 与 value/label），
                      // 任一缺失都会渲染成空白按钮且点击无效 ——
                      // 这里统一兜底，别让用户面对一个点不动的选项。
                      // 用 String() 而不是模板里的 :g 格式 ——
                      // ${x:g} 在 TSX 里会被解析成三元运算符，报语法错。
                      const kcalText =
                        o.kcal_per_100g != null
                          ? " · " + String(o.kcal_per_100g) + " 千卡/100g"
                          : "";
                      const text =
                        o.label ||
                        (o.name ? o.name + kcalText : "") ||
                        o.value ||
                        o.name ||
                        "";
                      if (!text) return null;
                      const key = o.value ?? String(o.id ?? i);
                      return (
                        <Tag
                          key={key}
                          color="green"
                          style={{ cursor: "pointer", padding: "3px 10px" }}
                          onClick={() => !loading && send(text)}
                        >
                          {text}
                        </Tag>
                      );
                    })}
                  </div>
                </div>
              ) : null}

              {m.citations && m.citations.length > 0 && (
                <details style={{ marginTop: 8 }}>
                  <summary style={{ cursor: "pointer", fontSize: 12, color: "#6b7280" }}>
                    数据来源（{m.citations.length} 条）
                  </summary>
<div className="msg-toolbar">
                        {m.citations.map((c, k) => {
                          // name 可能缺失（后端某些引用只给 items）。
                          // 直接 c.name.slice 会抛 TypeError，
                          // React 未捕获异常会卸载整棵树 → **整页白屏**。
                          // 这里逐字段兜底，缺什么显示什么。
                          const label = c.name || c.items?.join("、") || "数据来源";
                          const text =
                            typeof label === "string" ? label.slice(0, 40) : "数据来源";
                          return (
                            <Tag key={k} variant="filled">
                              [{c.type}] {text}
                            </Tag>
                          );
                        })}
                      </div>
                </details>
              )}

              {m.toolData && m.toolData.length > 0 && (
                <details style={{ marginTop: 6 }}>
                  <summary style={{ cursor: "pointer", fontSize: 12, color: "#6b7280" }}>
                    本次数据库返回值（数字来源）
                  </summary>
                  <div className="sql-box" style={{ marginTop: 6 }}>
                    {JSON.stringify(m.toolData, null, 2)}
                  </div>
                </details>
              )}

              {m.guardWarning && (
                <div className="trust-bar">
                  <InfoCircleOutlined /> {m.guardWarning}
                </div>
              )}
            </div>
          ),
        )}
        {loading && !msgs.some((m) => m.streaming) && <Spin size="small" />}
      </div>

      <div className="chat-input-bar">
        <Input.TextArea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="输入问题，Enter 发送，Shift+Enter 换行"
          autoSize={{ minRows: 2, maxRows: 5 }}
          onPressEnter={(e) => {
            if (!e.shiftKey) {
              e.preventDefault();
              send(input);
            }
          }}
        />
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 8, gap: 8 }}>
          {loading ? (
            <Button danger onClick={() => abortRef.current?.abort()}>
              停止生成
            </Button>
          ) : null}
          <Button
            type="primary"
            icon={<SendOutlined />}
            disabled={!input.trim() || loading}
            onClick={() => send(input)}
          >
            发送
          </Button>
        </div>
      </div>
    </div>
  );
}

/** 设计说明接口拿的是纯 JSON，直接 fetch 就行 */
async function httpGetJson(url: string): Promise<{ notes?: string[] } | null> {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

export default function ChatPage() {
  return (
    <ErrorBoundary title="对话页面出错">
      <ChatPageInner />
    </ErrorBoundary>
  );
}
