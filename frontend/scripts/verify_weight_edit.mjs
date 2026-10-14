/** 验收：体重历史可修改/删除 */
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
page.on("pageerror", (e) => errors.push(String(e).slice(0, 120)));

// 进记录页 → 体重标签
await page.goto(`${BASE}/record`, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);
await page.locator(".ant-segmented-item:has-text('体重')").click();
await page.waitForTimeout(2500);

// 记录两行才够看历史
const rows = page.locator(".ant-table-tbody tr.ant-table-row");
check("体重历史有数据", (await rows.count()) > 0, `${await rows.count()} 行`);
check("有「修改」按钮", (await page.locator("button:has-text('修改')").count()) > 0);
check("有删除按钮", (await page.locator(".ant-table-tbody button.ant-btn-dangerous").count()) > 0);

const firstRow = rows.first();
const dateText = (await firstRow.locator("td").first().innerText()).trim();
const origKg = (await firstRow.locator("td").nth(1).innerText()).trim();
const origBmi = (await firstRow.locator("td").nth(2).innerText()).trim();
console.log(`   目标行: ${dateText} ${origKg} BMI=${origBmi}`);

// 点修改 → 弹窗
await firstRow.locator("button:has-text('修改')").click();
await page.waitForTimeout(1200);
check("修改弹窗出现", (await page.locator(".ant-modal:has-text('修改')").count()) > 0);
const modalText = await page.locator(".ant-modal-body").innerText();
check("弹窗显示原值", modalText.includes(origKg.replace(" kg", "")) || modalText.includes("原值"), modalText.split("\n")[0].slice(0, 50));
check("弹窗标题含日期", (await page.locator(".ant-modal-title").innerText()).includes(dateText));

// 改成 77.5
const input = page.locator(".ant-modal-body input").first();
await input.fill("");
await input.type("77.5");
await page.waitForTimeout(400);
await page.locator(".ant-modal-footer .ant-btn-primary").click();
await page.waitForTimeout(2500);

// 验证表格已更新
const newRow = page.locator(".ant-table-tbody tr.ant-table-row").first();
const newKg = (await newRow.locator("td").nth(1).innerText()).trim();
const newBmi = (await newRow.locator("td").nth(2).innerText()).trim();
check("体重已改为 77.5", newKg === "77.5kg", `${origKg} → ${newKg}`);
check("BMI 已重算", newBmi !== origBmi, `BMI ${origBmi} → ${newBmi}`);

await page.screenshot({ path: "screenshots/weight-edit.png", fullPage: true });

// 还原
await page.locator(".ant-table-tbody tr.ant-table-row").first().locator("button:has-text('修改')").click();
await page.waitForTimeout(1000);
const origNum = origKg.replace("\s*kg", "");
const inp2 = page.locator(".ant-modal-body input").first();
await inp2.fill("");
await inp2.type(origNum);
await page.locator(".ant-modal-footer .ant-btn-primary").click();
await page.waitForTimeout(2200);
const restored = (await page.locator(".ant-table-tbody tr.ant-table-row").first().locator("td").nth(1).innerText()).trim();
check("已还原原值", restored === origKg, `${restored}（原 ${origKg}）`);

// 校验看板的 BMR 也跟着变了（体重影响 BMR）
const bmrChanged = await page.evaluate(async () => {
  const a = await (await fetch("/api/profile")).json();
  return a.bmr_sources?.expression || "";
});
check("BMR 依据可查（体重口径一致）", bmrChanged.includes("BMR = 10×"), bmrChanged.slice(0, 46));

check("无页面错误", errors.length === 0, errors.slice(0, 2).join(" | "));

await browser.close();
const failed = results.filter((r) => !r).length;
console.log(`\n结果：${results.length - failed}/${results.length} 通过`);
if (failed) process.exit(1);
