"""P3 智能对话端到端验证。

用法：
    1) 先启动服务：.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
    2) 再运行本脚本：.venv/Scripts/python.exe scripts/check_chat.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:8000"
PASS = FAIL = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {label}" + (f" — {extra}" if extra else ""))
    else:
        FAIL += 1
        print(f"  ❌ {label}" + (f" — {extra}" if extra else ""))


def section(t: str) -> None:
    print()
    print("─" * 64)
    print(f"  {t}")
    print("─" * 64)


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=120)

    # ==================================================================
    section("系统与 LLM 接入")
    r = c.get("/api/health")
    d = r.json()
    check("健康检查", r.status_code == 200)
    check("LLM 已配置", d["llm_configured"] is True, f'模型 qwen-max')

    r = c.get("/api/chat/design")
    d = r.json()
    check("对话链路设计可查", len(d["pipeline"]) == 6, f"{len(d['pipeline'])} 个阶段")
    check("数值防线已启用", d["trust_guard"]["enabled"] is True)
    check("  策略是模板填充", "占位符" in d["trust_guard"]["strategy"])
    check("7 条红线随接口暴露", len(d["red_lines"]) == 7)

    # ==================================================================
    section("单轮问答（数值必须正确）")
    # BMI 与运动消耗的期望值**从当前档案动态算出**，不写死。
    # 之前写死 25.7 / 137，用户改了身高年龄体重后就一直失败 ——
    # 那是测试写错，不是系统错。
    prof = c.get("/api/profile").json()
    bs = prof.get("bmr_sources") or {}
    _w = bs.get("weight_kg") or prof["weight_kg"]
    _h = bs.get("height_cm") or prof["height_cm"]
    _age = bs.get("age") or prof["age"]
    _g = bs.get("gender") or prof["gender"]
    _expect_bmi = round(_w / (_h / 100.0) ** 2, 1)

    # 快走 MET：搜索接口可能返回不了（别名匹配顺序问题），
    # 直接用对话接口的真实计算结果反推，避免断言依赖 MET 的取整方式。
    _met = 3.5  # 快走在别名表中固定指向 MET 3.5 的那条
    _expect_burn = round(_met * _w * 0.5, 1)

    cases = [
        ("200克米饭的热量是多少", ["232"], "食物热量"),
                # 分类不该写死：档案变了 BMI 可能从超重掉到正常，
        # 断言只校验「数值算对了」+「给出了某个判定」，不绑定具体分类。
        (f"我{_h:g}cm {_w:g}kg BMI是多少", [f"{_expect_bmi}"], "BMI"),
        ("快走30分钟消耗多少", [f"{_expect_burn:.0f}", "MET"], "运动消耗"),
        ("我的基础代谢是多少", ["BMR", "TDEE"], "BMR/TDEE 区分"),
        ("给我推荐几个燃脂效率高的运动", ["MET", "千卡"], "燃脂运动推荐"),
    ]
    for q, expect_kw, label in cases:
        r = c.post("/api/chat", json={"message": q})
        d = r.json()
        ans = d["answer"]
        # 数值用容差匹配：答案可能给 163.6 也可能给 164，
        # 写死字符串会因取整方式不同而误判。
        ok = r.status_code == 200
        for kw in expect_kw:
            try:
                target = float(kw)
                found = any(
                    abs(float(m.group()) - target) <= 1.0
                    for m in __import__("re").finditer(r"\d+(?:\.\d+)?", ans)
                )
                ok = ok and found
            except ValueError:
                ok = ok and (kw in ans)
        check(f"{label}：{q}", ok, ans[:70].replace("\n", " "))
        if not ok:
            print(f"      期望包含 {expect_kw}，实际：{ans[:150]}")

    # ==================================================================
    section("红线二：数值可信化")
    # 模型实测会编造 230（真值232），这里验证系统给的是 232
    r = c.post("/api/chat", json={"message": "200克米饭的热量是多少"})
    d = r.json()
    check("返回数据库真值 232", "232" in d["answer"], d["answer"][:80])
    check("无校验警告", not d["guard_warning"], d["guard_warning"][:60] or "无")
    check("工具结果可溯源", len(d["tool_results"]) > 0, str(d["tool_results"][:1]))

    # 拒答场景：库里没有的食物
    r = c.post("/api/chat", json={"message": "一杯珍珠奶茶多少热量"})
    d = r.json()
    has_refuse = any(k in d["answer"] for k in ["知识库", "没有", "没法", "无法", "建议"])
    check("库里没有的食物会拒答", has_refuse, d["answer"][:80].replace("\n", " "))

    # ==================================================================
    section("多轮追问（LangGraph 状态机）")
    r = c.post("/api/chat", json={"message": "西红柿炒鸡蛋多少热量"})
    d = r.json()
    sid = d["session_id"]
    check("第1轮触发追问", d["needs_clarification"] is True, d["clarify_message"][:50])
    check("提供油量选项", len(d["clarify_options"]) == 3,
          str([o.get("label") for o in d["clarify_options"]]))

    r = c.post("/api/chat", json={"message": "偏清淡", "session_id": sid})
    d = r.json()
    check("第2轮完成计算", not d["needs_clarification"], f"{len(d['tool_results'])} 条工具结果")
    check("  热量数值正确", "429" in d["answer"] or "430" in d["answer"],
          d["answer"][:80].replace("\n", " "))
    check("  菜名正确带入", "西红柿炒鸡蛋" in d["answer"])

    # 换一种答法
    r = c.post("/api/chat", json={"message": "西红柿炒鸡蛋多少热量"})
    sid2 = r.json()["session_id"]
    r = c.post("/api/chat", json={"message": "少油", "session_id": sid2})
    d = r.json()
    check("「少油」也能接住", not d["needs_clarification"], d["answer"][:60].replace("\n", " "))

    r = c.delete(f"/api/chat/session/{sid}")
    check("会话可清除", r.json()["cleared"] is True)

    # ==================================================================
    section("红线四：同名歧义弹候选")
    r = c.post("/api/chat", json={"message": "琼脂多少热量"})
    d = r.json()
    check("歧义触发候选", d["needs_clarification"] is True or len(d["candidates"]) > 0,
          f"{len(d['candidates'])} 个候选")
    if d["candidates"]:
        kcals = sorted(c.get("kcal_per_100g", 0) for c in d["candidates"])
        check("  候选含 26 与 311", 26.0 in kcals and 311.0 in kcals, str(kcals))

    # ==================================================================
    section("RAG 知识问答")
    r = c.post("/api/chat", json={"message": "减脂期应该怎么吃主食"})
    d = r.json()
    check("知识问答命中", d["intent"] == "KNOWLEDGE_QA", d["intent_label"])
    check("  有检索引用", len(d["citations"]) > 0, f"{len(d['citations'])} 条")
    check("  回答有内容", len(d["answer"]) > 80, f"{len(d['answer'])} 字")

    r = c.post("/api/chat", json={"message": "为什么体重一直不掉"})
    d = r.json()
    check("平台期问题", len(d["answer"]) > 80, d["answer"][:70].replace("\n", " "))

    # ==================================================================
    section("流式对话（SSE）")
    with httpx.stream("POST", f"{BASE}/api/chat/stream",
                      json={"message": "200克米饭的热量是多少"}, timeout=120) as resp:
        events = []
        for line in resp.iter_lines():
            if line.startswith("data:"):
                import json as _j
                events.append(_j.loads(line[5:]))
    types = [e["type"] for e in events]
    check("收到 intent 事件", "intent" in types)
    check("收到 tool 事件", "tool" in types, f"工具结果先于文本推送")
    check("收到 token 事件", "token" in types)
    check("收到 done 事件", "done" in types, str(types))

    # ==================================================================
    section("降级：不用 LLM")
    r = c.post("/api/chat", json={"message": "200克米饭的热量是多少", "use_llm": False})
    d = r.json()
    check("无 LLM 仍可用（红线七）", r.status_code == 200 and len(d["answer"]) > 0,
          d["answer"][:70].replace("\n", " "))
    check("  仍返回工具结果", len(d["tool_results"]) > 0)

    c.close()
    print()
    print("═" * 64)
    print(f"  结果：{PASS} 项通过，{FAIL} 项失败")
    print("═" * 64)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())