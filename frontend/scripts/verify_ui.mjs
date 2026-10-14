/**
 * 前端真实验收：用 Chromium 打开三个页面，抓控制台错误与关键内容。
 *
 * 为什么要真浏览器：类型检查和构建通过只证明「能编译」，
 * 不证明「能跑」—— 运行时引用未定义变量、请求路径错、
 * 图表容器高度为 0 这类问题只有真实渲染才暴露。
 */
import { chromium } from "playwright";

const BASE = process.argv[2] || "http://127.0.0.1:5173";
const results = [];

function check(label, ok, detail = "") {
  results.push({ label, ok, detail });
  console.log(`${ok ? "✅" : "❌"} ${label}${detail ? " — " + detail : ""}`);
}

const browser = await chromium.launch({ channel: "msedge" });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await ctx.newPage();

const errors = [];
page.on("console", (m) => {
  if (m.type() === "error") errors.push(m.text().slice(0, 200));
});
page.on("pageerror", (e) => errors.push("PAGEERROR: " + String(e).slice(0, 200)));

// ── 1. 看板页 ──
await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);

check("看板页标题渲染", (await page.locator(".app-logo").innerText()).includes("热量记录"));
check(
  "今日摄入卡片有数值",
  /\d/.test(await page.locator(".stat-card").first().innerText()),
  (await page.locator(".stat-card").first().innerText()).replace(/\n/g, " ").slice(0, 60),
);
check(
  "BMR 与 TDEE 同时显示",
  (await page.content()).includes("基础代谢") && (await page.content()).includes("日常总消耗"),
);
check("体重折线渲染 canvas", (await page.locator("canvas").count()) >= 2, `${await page.locator("canvas").count()} 个 canvas`);
check(
  "推荐表格有数据行",
  (await page.locator(".ant-table-tbody tr.ant-table-row").count()) > 0,
  `${await page.locator(".ant-table-tbody tr.ant-table-row").count()} 行`,
);
check(
  "后端数据量标签可见",
  (await page.locator(".ant-tag").first().innerText()).includes("食物"),
  await page.locator(".ant-tag").first().innerText(),
);

// ── 2. 记录页 ──
await page.click('a[href="/record"]');
await page.waitForTimeout(2000);

check("记录页三个标签页", (await page.locator(".ant-segmented-item").count()) === 3);
check(
  "饮食表单有输入框",
  (await page.locator("input").count()) > 0,
  `${await page.locator("input").count()} 个 input`,
);
// 先自举一条今日饮食记录，再断言列表非空 ——
// 直接断言「今天有记录」会让验收依赖数据库当前状态，
// 用户清过数据或换一天跑就会莫名失败。
const todayIntake = await page.evaluate(async () => {
  const d = new Date().toISOString().slice(0, 10);
  // lookup 返回 { found, food: {...} }，取嵌套里的 name
  const lookup = await (await fetch("/api/foods/lookup/" + encodeURIComponent("米饭"))).json();
  const name = lookup?.food?.name;
  if (!name) return { skipped: true, reason: "lookup 无 food 字段" };
  // IntakeCreate 用 food_name（食物名或别名），不是 id
  const r = await fetch("/api/intake", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ food_name: name, grams: 100, log_date: d, source: "verify_ui" }),
  });
  return { status: r.status, date: d, name };
});
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(1500);
check(
  "今日饮食记录列表非空",
  (await page.content()).includes("千卡"),
  todayIntake.skipped ? `自举失败: ${todayIntake.reason}` : `自举 ${todayIntake.name} HTTP ${todayIntake.status}`,
);

// 触发菜谱计算，验证追问交互
await page.fill('input[placeholder*="输入菜名"]', "西红柿炒鸡蛋");
await page.locator("button:has-text('计算')").click();
await page.waitForTimeout(3000);
const hasClarify = (await page.content()).includes("请选择做法") || (await page.content()).includes("用油量");
check("菜谱计算触发追问交互", hasClarify);

// 选做法 + 油量 → 应弹出确认框
// 注意：antd 的下拉渲染在 portal 里，DOM 中会同时存在隐藏的历史 dropdown，
// 必须限定「未隐藏」的那个，否则点到不可见元素会一直超时。
async function pickSelect(triggerText, optIndex = 0) {
  const trigger = page.locator(".ant-select").filter({ hasText: triggerText }).first();
  await trigger.click();
  await page.waitForTimeout(700);
  const dd = page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden)").last();
  const opt = dd.locator(".ant-select-item-option").nth(optIndex);
  await opt.waitFor({ state: "visible", timeout: 8000 });
  await opt.click();
  await page.waitForTimeout(700);
}

try {
  await pickSelect("选择烹饪方式", 0);
  check("可选择烹饪方式", true);
} catch {
  check("可选择烹饪方式", false, "下拉未展开");
}

try {
  await pickSelect("留空则计算时会追问", 0);
  check("可选择用油量", true);
} catch {
  check("可选择用油量", false, "下拉未展开");
}

await page.locator("button:has-text('计算')").click();
await page.waitForTimeout(3500);
const confirmOpen = (await page.locator(".ant-modal-confirm, .ant-modal").count()) > 0;
check("两阶段确认弹窗出现", confirmOpen);
if (confirmOpen) {
  const modalText = await page.locator(".ant-modal").first().innerText();
  check("弹窗含热量数值", /千卡/.test(modalText), modalText.replace(/\n/g, " ").slice(0, 100));
}

// 关闭弹窗
if (confirmOpen) {
  await page.locator(".ant-modal button").filter({ hasText: /再改改|取消/ }).first().click();
  await page.waitForTimeout(800);
}

// 运动页
await page.locator(".ant-segmented-item").filter({ hasText: "运动" }).click();
await page.waitForTimeout(1500);
check(
  "日常活动标签已渲染",
  (await page.locator(".ant-tag").count()) > 10,
  `${await page.locator(".ant-tag").count()} 个活动标签`,
);

// 体重页
await page.locator(".ant-segmented-item").filter({ hasText: "体重" }).click();
await page.waitForTimeout(1500);
check(
  "体重历史表格存在",
  (await page.locator(".ant-table").count()) > 0,
);

// ── 3. 对话页 ──
await page.click('a[href="/chat"]');
await page.waitForTimeout(1500);
check("对话页预设问题存在", (await page.locator(".ant-tag").count()) >= 6);

await page.fill('textarea[placeholder*="输入问题"]', "200克米饭的热量是多少");
await page.locator("button:has-text('发送')").click();
await page.waitForTimeout(2000);

// 等流式回答完成（最多 60 秒）
let answerText = "";
for (let i = 0; i < 30; i++) {
  answerText = await page.locator(".chat-bubble.assistant").last().innerText().catch(() => "");
  if (answerText.includes("千卡") && i > 3) break;
  await page.waitForTimeout(2000);
}
check("对话返回答案", answerText.length > 20, answerText.replace(/\n/g, " ").slice(0, 110));
check("答案含数据库真值 232", answerText.includes("232"), answerText.slice(0, 80));
check(
  "意图标签展示",
  (await page.locator(".ant-tag").filter({ hasText: "意图" }).count()) > 0,
);

// ── 4. 换一批 ──
await page.click('a[href="/"]');
await page.waitForTimeout(2500);

const recRowsBefore = await page.locator(".ant-table-tbody tr.ant-table-row").allInnerTexts();
check("换一批按钮存在", (await page.locator("button:has-text('换一批')").count()) > 0);
check(
  "推荐显示批次进度",
  /第\s*\d+-\d+\s*\/\s*\d+\s*条/.test(await page.content()),
  (await page.locator(".ant-card-extra").last().innerText()).replace(/\n/g, " ").slice(0, 40),
);

await page.locator("button:has-text('换一批')").click();
await page.waitForTimeout(2000);
const recRowsAfter = await page.locator(".ant-table-tbody tr.ant-table-row").allInnerTexts();
const changed = recRowsBefore.join("|") !== recRowsAfter.join("|");
check("换一批后内容变化", changed, changed ? recRowsAfter[0]?.replace(/\n/g, " ").slice(0, 50) : "内容未变");
check(
  "14 天图表",
  (await page.content()).includes("近 14 日摄入"),
  (await page.content()).match(/近 \d+ 日摄入/)?.[0] || "未找到",
);

// ── 5. 个人中心 ──
await page.click('a[href="/profile"]');
await page.waitForTimeout(2500);

check("个人中心标题", (await page.content()).includes("基本信息"));
check(
  "BMR 算式完整展示",
  (await page.content()).includes("BMR = 10×") && (await page.content()).includes("6.25×"),
  (await page.locator(".sql-box").first().innerText().catch(() => "无")).slice(0, 70),
);
check(
  "体重来源已标注",
  (await page.content()).includes("体重取自记录") || (await page.content()).includes("体重取自档案"),
);
check(
  "身高输入框存在",
  (await page.locator("input").count()) >= 6,
  `${await page.locator("input").count()} 个输入控件`,
);
check(
  "活动系数有选项",
  (await page.content()).includes("轻度活动"),
);
check("BMI 卡片存在", (await page.content()).includes("当前身体指标"));

// 表单初值必须有值。之前漏了这条断言，
// 导致「Space.Compact 挡住 InputNumber 导致数值字段全空」这个 bug 溜过去。
const filled = await page.locator(".ant-form-item").evaluateAll((els) =>
  els
    .map((e) => ({
      label: e.querySelector(".ant-form-item-label label")?.innerText?.trim() ?? "",
      value: e.querySelector("input")?.value ?? "",
      // antd v6 用 .ant-select-content-has-value 标记「已选中」，
      // 这个类名本身就是有值的判据（title 属性只在 label 是纯文本时才有）。
      shown: e.querySelector(".ant-select-content-has-value") ? "已选中" : "",
    }))
    .filter((i) => ["年龄", "身高", "当前体重", "目标", "活动系数"].some((k) => i.label.includes(k))),
);
const emptyFields = filled.filter((i) => !i.value && !i.shown).map((i) => i.label);
check(
  "表单字段有初值",
  filled.length >= 5 && emptyFields.length === 0,
  emptyFields.length ? `空字段: ${emptyFields.join("/")}` : filled.map((i) => `${i.label}=${i.value || i.shown}`).join(" "),
);
await page.screenshot({ path: "screenshots/profile.png", fullPage: true });

await page.screenshot({ path: "screenshots/chat.png", fullPage: false });
await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);
await page.screenshot({ path: "screenshots/dashboard.png", fullPage: false });
await page.click('a[href="/record"]');
await page.waitForTimeout(2000);
await page.screenshot({ path: "screenshots/record.png", fullPage: false });

// ── 控制台错误 ──
const realErrors = errors.filter(
  (e) => !e.includes("favicon") && !e.includes("DevTools") && !e.includes("Download the React DevTools"),
);
check("无控制台错误", realErrors.length === 0, realErrors.slice(0, 3).join(" | ") || "干净");

await browser.close();

const failed = results.filter((r) => !r.ok);
console.log(`\n结果：${results.length - failed.length}/${results.length} 通过`);
if (failed.length) {
  console.log("失败项：");
  failed.forEach((f) => console.log(`  - ${f.label} ${f.detail}`));
  process.exit(1);
}
