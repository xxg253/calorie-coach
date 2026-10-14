import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 后端 API 地址。开发时走代理，避免 CORS 与硬编码地址。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: false,
    // 由启动脚本统一打开浏览器。
    // 这里若保持默认的 open:true，vite 会自己开 localhost:5173，
    // 与脚本里的 127.0.0.1:5173 会被浏览器当成两个源 → 两个标签页。
    open: false,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        // SSE 需要关闭缓冲，否则流式响应会被攒到最后一次性吐出
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes) => {
            if (proxyRes.headers["content-type"]?.includes("text/event-stream")) {
              proxyRes.headers["x-accel-buffering"] = "no";
            }
          });
        },
      },
    },
  },
  build: {
    outDir: "dist",
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        // Vite 8 的 rolldown 只接受函数形式，对象形式会直接抛
        // "manualChunks is not a function"。
        manualChunks(id: string) {
          if (id.includes("node_modules/echarts") || id.includes("node_modules/zrender")) {
            return "echarts";
          }
          if (id.includes("node_modules/antd") || id.includes("node_modules/@ant-design")) {
            return "antd";
          }
          if (id.includes("node_modules/react")) {
            return "react";
          }
          return undefined;
        },
      },
    },
  },
});
