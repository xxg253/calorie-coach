import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** 界面上给用户看的说明 */
  title?: string;
}

interface State {
  error: Error | null;
  info: string;
}

/**
 * 渲染错误边界。
 *
 * ## 为什么必须有
 *
 * React 里任何一个渲染期异常都会**卸载整棵组件树** ——
 * 不是只崩掉出错的那个组件，而是整个页面变空白，连输入框都没有。
 *
 * 实测踩过两次：后端 citation 缺 name，前端 `c.name.slice(0, 40)`
 * 抛 TypeError → 整页白屏（用户描述「答完突然大白屏」）。
 *
 * 单个字段的防御能修这一次，但下次别处再漏一个字段又会白屏。
 * 边界保证：**任何渲染异常都降级为一个提示，而不是白屏**。
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, info: "" };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 保留栈信息便于排查，但只展示必要部分给用户
    this.setState({ info: (info.componentStack ?? "").slice(0, 300) });
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary] 渲染异常:", error, info);
  }

  render(): ReactNode {
    const { error, info } = this.state;
    if (!error) return this.props.children;

    return (
      <div style={{ padding: 16, fontFamily: "system-ui, sans-serif" }}>
        <div
          style={{
            border: "1px solid #fca5a5",
            background: "#fef2f2",
            borderRadius: 8,
            padding: 16,
            color: "#7f1d1d",
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 8 }}>
            {this.props.title ?? "页面渲染出错"}
          </div>
          <div style={{ fontSize: 13, lineHeight: 1.6 }}>
            对话记录仍在后端保存，可刷新页面继续。
          </div>
          <button
            onClick={() => this.setState({ error: null, info: "" })}
            style={{
              marginTop: 12,
              padding: "6px 14px",
              borderRadius: 6,
              border: "1px solid #dc2626",
              background: "#fff",
              color: "#b91c1c",
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            重试
          </button>
          <details style={{ marginTop: 12, fontSize: 12, color: "#991b1b" }}>
            <summary style={{ cursor: "pointer" }}>技术详情</summary>
            <pre style={{ whiteSpace: "pre-wrap", marginTop: 6 }}>
              {error.message}
              {"\n"}
              {info}
            </pre>
          </details>
        </div>
      </div>
    );
  }
}