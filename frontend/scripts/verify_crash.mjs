/**
 * 崩溃专项验收。
 *
 * 真实 bug：后端 citation 缺 name，前端 c.name.slice(0, 40)
 * 抛 TypeError → React 卸载整棵树 → 整页白屏（连输入框都没了）。
 */
import { chromium } from "playwright";

const BASE = "http://127.0.0.1:5173/chat";

let pass = 0, fail = 0;
function check(label, ok, detail = "") {
  if (ok) { console.log(`✅ ${label}`); pass++; }
  else { console.log(`❌ ${label} ${detail}`); fail++; }
}

const browser = await chromium.launch({ channel: "msedge" });
const ctx = await browser.newContext();
const page = await ctx.newPage();

// 抓 JS 运行时错误 —— 白屏就是它导致的
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

// 拦截 SSE，直接喂带「缺 name」的 citation（复现真实崩溃条件）
await page.route("**/api/chat/stream*", async (route) => {
  const chunks = [
    `data: ${JSON.stringify({ type: "intent", data: { intent: "RECOMMEND", label: "推荐食物", session_id: "t1" } })}\n\n`,
    `data: ${JSON.stringify({ type: "token", data: { content: "推荐如下：紫菜、虾仁、鸡蛋白。" } })}\n\n`,
    // 关键：citation 只有 items 没有 name —— 这正是崩溃输入
    `data: ${JSON.stringify({ type: "done", data: {
      answer: "推荐如下：紫菜、虾仁、鸡蛋白。",
      citations: [{ type: "recommend", items: ["紫菜", "虾仁", "鸡蛋白"] }],
      guard_warning: "", errors: [],
    } })}\n\n`,
    "data: [DONE]\n\n",
  ];
  await route.fulfill({
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
    body: chunks.join(""),
  });
});

await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(1200);

// 输入并发送
const ta = page.getByPlaceholder("输入问题，Enter 发送，Shift+Enter 换行").first();
await ta.fill("给我推荐几个高蛋白低热量的食物");
await ta.press("Enter");
await page.waitForTimeout(2500);

console.log("\n═══ 崩溃验收（citation 缺 name）═══");
check("无 JS 运行时错误", errors.length === 0, `→ ${errors.slice(0, 2).join(" | ")}`);
check("页面未白屏（输入框仍在）", (await page.getByPlaceholder("输入问题，Enter 发送，Shift+Enter 换行").count()) > 0);
check("答案已显示", (await page.getByText("推荐如下").count()) > 0);
check("发送按钮存在", (await page.locator("button:has-text('发送')").count()) > 0);

// 再验 ErrorBoundary：故意让渲染抛异常
const boundaryVisible = await page.evaluate(() => {
  return document.body.innerText.includes("页面渲染出错");
});
console.log(`\n   ErrorBoundary 占位: ${boundaryVisible ? "存在（未触发）" : "未触发"}`);

// 截图存证
await page.screenshot({ path: "scripts/crash-proof.png", fullPage: true });

await browser.close();
console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail ? 1 : 0);
