/**
 * 验收：切页不丢对话。
 * 问一轮 → 切到看板 → 切回对话 → 内容仍在。
 */
import { chromium } from "playwright";

const BASE = "http://127.0.0.1:5173";
const results = [];
const check = (label, ok, detail = "") => {
  results.push(ok);
  console.log(`${ok ? "✅" : "❌"} ${label}${detail ? " — " + detail : ""}`);
};

const browser = await chromium.launch({ channel: "msedge" });
const page = await browser.newContext({ viewport: { width: 1440, height: 1000 } }).then((c) => c.newPage());
const errors = [];
page.on("pageerror", (e) => errors.push(String(e).slice(0, 150)));

// 1. 进对话页，清掉可能残留的缓存，保证从零开始
await page.goto(`${BASE}/chat`, { waitUntil: "networkidle" });
await page.evaluate(() => localStorage.clear());
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(3000);

// 2. 问一轮
await page.fill('textarea[placeholder*="输入问题"]', "200克米饭的热量是多少");
await page.locator("button:has-text('发送')").click();

// 等回答完成
let answer = "";
for (let i = 0; i < 30; i++) {
  answer = await page.locator(".chat-bubble.assistant").last().innerText().catch(() => "");
  if (answer.includes("千卡") && i > 3) break;
  await page.waitForTimeout(2000);
}
check("第一轮回答成功", answer.includes("232"), answer.slice(0, 60).replace(/\n/g, " "));
const beforeCount = await page.locator(".chat-bubble").count();

// 3. 切到看板
await page.click('a[href="/"]');
await page.waitForTimeout(2000);
check("已切到看板", (await page.content()).includes("今日摄入"), "");

// 4. 切回对话
await page.click('a[href="/chat"]');
await page.waitForTimeout(2500);
const afterCount = await page.locator(".chat-bubble").count();
const afterAnswer = await page.locator(".chat-bubble.assistant").last().innerText().catch(() => "");

check("切回后消息数量不变", afterCount === beforeCount, `切走前 ${beforeCount} / 切回后 ${afterCount}`);
check("切回后回答内容仍在", afterAnswer.includes("232"), afterAnswer.slice(0, 60).replace(/\n/g, " "));

// 5. 更狠的验证：整页刷新
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(3000);
const afterReload = await page.locator(".chat-bubble").count();
const reloadAnswer = await page.locator(".chat-bubble.assistant").last().innerText().catch(() => "");
check("刷新后消息仍在", afterReload === beforeCount, `刷新后 ${afterReload} 条`);
check("刷新后内容正确", reloadAnswer.includes("232"), reloadAnswer.slice(0, 50).replace(/\n/g, " "));

// 6. localStorage 里确实存了
const stored = await page.evaluate(() => {
  const raw = localStorage.getItem("calorie-chat-state");
  if (!raw) return null;
  const p = JSON.parse(raw);
  return { sessionId: p.sessionId, count: p.msgs?.length };
});
check("localStorage 已保存", !!stored && stored.count > 0, JSON.stringify(stored));

// 7. 换浏览器场景：清掉 localStorage，靠后端历史恢复
await page.evaluate(() => localStorage.clear());
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(4000);
const fromBackend = await page.locator(".chat-bubble").count();
check(
  "清缓存后从后端恢复",
  fromBackend > 0,
  `${fromBackend} 条（后端历史）`,
);

await page.screenshot({ path: "screenshots/chat-restored.png" });
check("无页面错误", errors.length === 0, errors.slice(0, 2).join(" | "));

await browser.close();
const failed = results.filter((r) => !r).length;
console.log(`\n结果：${results.length - failed}/${results.length} 通过`);
if (failed) process.exit(1);
