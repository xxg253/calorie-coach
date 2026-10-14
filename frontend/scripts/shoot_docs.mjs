/**
 * 生成 README 用的界面截图。
 *
 * 输出到 docs/images/，供 README 引用。
 * 用真实后端数据，不造假。
 */
import { chromium } from "playwright";
import { mkdirSync } from "fs";

const OUT = "../docs/images";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ channel: "msedge" });
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
});
const page = await ctx.newPage();

async function shot(path, wait = 1500) {
  await page.waitForTimeout(wait);
  await page.screenshot({ path: `${OUT}/${path}` });
  console.log("📸", path);
}

// 1) 看板
await page.goto("http://localhost:5173/", { waitUntil: "networkidle" });
await shot("dashboard.png", 2500);

// 2) 智能问答 —— 用一个能体现「数值可信化」的问题
await page.goto("http://localhost:5173/chat", { waitUntil: "networkidle" });
await page.waitForTimeout(1200);

const qs = [
  ["200克米饭的热量是多少", "chat-lookup.png"],
  ["请问我吃200克苹果，要跳绳多长时间才能消耗", "chat-burnoff.png"],
  ["快走30分钟消耗多少", "chat-exercise.png"],
];

for (const [q, name] of qs) {
  const ta = page.getByPlaceholder("输入问题，Enter 发送，Shift+Enter 换行").first();
  await ta.fill(q);
  await ta.press("Enter");
  // 等回答完成（发送按钮重新可点= loading 结束）
  for (let i = 0; i < 60; i++) {
    await page.waitForTimeout(1000);
    const loading = await page.locator("button:has-text('停止生成')").count();
    if (loading === 0) break;
  }
  await shot(name, 800);
}

await browser.close();
console.log("✅ 截图完成 →", OUT);