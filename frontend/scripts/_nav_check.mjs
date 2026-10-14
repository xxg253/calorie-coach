/** 验证 CSS 方案：导航 4 项在各种宽度都完整可见，无「…」 */
import { chromium } from "playwright";

const b = await chromium.launch({ channel: "msedge" });
const bad = [];

for (const w of [1600, 1280, 1024, 900, 780, 640]) {
  const page = await b
    .newContext({ viewport: { width: w, height: 700 } })
    .then((c) => c.newPage());
  const errs = [];
  page.on("console", (m) => {
    if (m.type() === "error") errs.push(m.text().slice(0, 80));
  });
  await page.goto("http://127.0.0.1:5173/", { waitUntil: "networkidle" });
  await page.waitForTimeout(1800);

  // 只数「视觉上真正可见」的菜单项
  const visible = await page.evaluate(() => {
    const els = [...document.querySelectorAll(".ant-menu-item, .ant-menu-submenu")];
    return els
      .filter((e) => {
        const r = e.getBoundingClientRect();
        const st = getComputedStyle(e);
        return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
      })
      .map((e) => e.textContent.trim())
      .filter(Boolean);
  });
  const ellipsis = await page.getByText("...", { exact: true }).count().catch(() => 0);
  const ok = visible.length === 4 && ellipsis === 0 && errs.length === 0;
  if (!ok) bad.push(w);
  console.log(
    `${String(w).padStart(4)}px → 可见 ${visible.length} 项 [${visible.join("/")}] | "…" ${ellipsis} | ${ok ? "✅" : "⚠️ " + errs[0] || ""}`,
  );
  if (w === 900) await page.screenshot({ path: "screenshots/_nav900.png" });
  await page.close();
}

await b.close();
console.log(`\n结果：${6 - bad.length}/6 宽度正常${bad.length ? " 失败: " + bad.join(",") : ""}`);
if (bad.length) process.exit(1);
