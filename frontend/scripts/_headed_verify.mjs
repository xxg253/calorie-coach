/** 有头模式验证导航 —— 无头浏览器复现不了真实浏览器的问题，必须用有头 */
import { chromium } from "playwright";
const b = await chromium.launch({ channel:"msedge", headless:false });
const bad = [];
const W = [1920, 1600, 1366, 1280, 1100, 1024, 900, 800, 700];
for (const w of W) {
  const page = await b.newContext({ viewport:{width:w,height:800} }).then(c=>c.newPage());
  const errs = [];
  page.on("pageerror", e => errs.push(String(e).slice(0,60)));
  page.on("console", m => { if (m.type()==="error") errs.push(m.text().slice(0,60)); });
  await page.goto("http://localhost:5173/", { waitUntil:"networkidle" });
  await page.waitForTimeout(1800);
  const r = await page.evaluate(() => {
    const vis = (el) => {
      const q = el.getBoundingClientRect();
      if (q.width<=0||q.height<=0) return false;
      let n = el;
      while (n && n!==document.body) {
        const s = getComputedStyle(n);
        if (s.opacity==="0"||s.display==="none"||s.visibility==="hidden") return false;
        n = n.parentElement;
      }
      return true;
    };
    const items = [...document.querySelectorAll(".app-nav-item")];
    const visible = items.filter(vis).map(e => e.textContent.trim());
    const off = items.filter(e => e.getBoundingClientRect().right > innerWidth).map(e=>e.textContent.trim());
    return { count: items.length, visible, off };
  });
  const ok = r.count===4 && r.visible.length===4 && !r.off.length && !errs.length;
  if (!ok) bad.push(w);
  console.log(`${String(w).padStart(4)}px → DOM ${r.count} 项, 可见 ${r.visible.length} [${r.visible.join("/")}] ${r.off.length?"⚠️溢出":"✅"} ${ok?"✅":"❌ "+(errs[0]||"")}`);
  if (w===1440 || w===1366) await page.screenshot({path:`screenshots/_h${w}.png`, clip:{x:0,y:0,width:w,height:100}});
  await page.close();
}
await b.close();
console.log(`\n结果：${W.length-bad.length}/${W.length} 通过${bad.length?"  失败:"+bad.join(","):""}`);
if (bad.length) process.exit(1);
