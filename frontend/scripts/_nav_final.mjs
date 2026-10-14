/** 终验：按真实渲染可见性判定导航 */
import { chromium } from "playwright";
const b = await chromium.launch({ channel: "msedge" });
const bad = [];
const W = [1600, 1280, 1024, 900, 780, 640, 480];
for (const w of W) {
  const page = await b.newContext({ viewport:{width:w,height:700} }).then(c=>c.newPage());
  const errs = [];
  page.on("console", m => { if (m.type()==="error") errs.push(m.text().slice(0,50)); });
  await page.goto("http://127.0.0.1:5173/", { waitUntil:"networkidle" });
  await page.waitForTimeout(1800);
  const r = await page.evaluate(() => {
    const vis = (el) => {
      const q = el.getBoundingClientRect();
      if (q.width<=0 || q.height<=0) return false;
      let n = el;
      while (n && n !== document.body) {
        const s = getComputedStyle(n);
        if (s.opacity==="0"||s.display==="none"||s.visibility==="hidden") return false;
        n = n.parentElement;
      }
      return true;
    };
    const expect = ["看板","记录","智能问答","个人中心"];
    const got = {};
    document.querySelectorAll(".ant-menu-item").forEach(el => {
      const t = el.textContent.trim();
      if (expect.includes(t)) got[t] = got[t] || vis(el);
    });
    const ell = document.querySelector(".anticon-ellipsis");
    return { visible: expect.filter(t=>got[t]), ellVisible: ell ? vis(ell.closest("li")||ell) : false };
  });
  const ok = r.visible.length===4 && !r.ellVisible && !errs.length;
  if (!ok) bad.push(w);
  console.log(`${String(w).padStart(4)}px → ${r.visible.length}/4 可见 | "…"${r.ellVisible?"⚠️":"已隐藏"} ${ok?"✅":"❌"}`);
  if (w===900) await page.screenshot({ path:"screenshots/_nav_final900.png" });
  await page.close();
}
await b.close();
console.log(`\n结果：${W.length-bad.length}/${W.length} 通过${bad.length?"  失败:"+bad.join(","):""}`);
if (bad.length) process.exit(1);
