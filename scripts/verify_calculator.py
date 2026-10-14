"""算术引擎安全性与正确性验证 —— 必须在接入护栏前证明它安全。"""

import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

from app.llm.calculator import evaluate

# 模拟槽位：这些才是可信原料
SLOTS = {
    "met_1": 3.5,
    "weight_1": 78.2,
    "kcal_1": 295.0,
    "minutes_1": 30.0,
}

ok = 0
fail = 0


def check(label, expr, expect=None, should_fail=False):
    global ok, fail
    r = evaluate(expr, SLOTS)
    if should_fail:
        if r.ok:
            print(f"❌ {label}: 应当拒绝但成功了 → {r.value}")
            fail += 1
        else:
            print(f"✅ {label}: 已拒绝（{r.reason}）")
            ok += 1
        return
    if not r.ok:
        print(f"❌ {label}: 意外失败 → {r.reason}")
        fail += 1
        return
    got = r.value
    if expect is not None and abs(got - expect) > 0.05:
        print(f"❌ {label}: {expr} = {got}，期望 {expect}")
        fail += 1
        return
    print(f"✅ {label}: {expr} = {got}")
    ok += 1


print("═══ 正确性 ═══")
check("四则运算", "met_1 * weight_1 * 0.5", 136.85)
check("除法", "kcal_1 / (met_1 * weight_1) * 60", 64.7)
check("括号优先级", "(met_1 + weight_1) * 2", 163.4)
check("一元负号", "-met_1 + 10", 6.5)
check("一元正号", "+met_1", 3.5)
check("多级嵌套", "((met_1 * weight_1) / 60) * minutes_1", 136.85)
check("整数结果不带小数", "met_1 * weight_1 * 0.5 / 100 * 100", 136.8)
check("常量运算", "2 * 3 + 4", 10)

print("\n═══ 必须拒绝的（安全边界）═══")
check("导入模块", "__import__('os').system('ls')", should_fail=True)
check("属性访问", "met_1.real", should_fail=True)
check("下标访问", "[1,2,3][0]", should_fail=True)
check("函数调用", "print('hi')", should_fail=True)
check("变量函数调用", "met_1.__class__", should_fail=True)
check("字符串常量", "'abc' + 'def'", should_fail=True)
check("未知变量", "unknown_var * 2", should_fail=True)
check("布尔常量", "True + 1", should_fail=True)
check("除以零", "met_1 / 0", should_fail=True)
check("除以极小数", "met_1 / 0.0000000000001", should_fail=True)
check("幂运算", "met_1 ** 100", should_fail=True)
check("位运算", "met_1 & 1", should_fail=True)
check("比较运算", "met_1 > 3", should_fail=True)
check("三元表达式", "1 if met_1 else 2", should_fail=True)
check("lambda", "lambda x: x", should_fail=True)
check("列表", "[1,2,3]", should_fail=True)
check("赋值", "x = 1", should_fail=True)
check("f字符串", "f'{met_1}'", should_fail=True)
check("空表达式", "", should_fail=True)
check("注入分号", "met_1; import os", should_fail=True)
check("超长表达式", "1+" * 150 + "1", should_fail=True)
check("极端放大结果", "met_1 / 0.0001 * 100000000000", should_fail=True)

print("\n═══ 极端输入不崩溃 ═══")
for expr in ["(((", ")))", "1/0", "nan", "inf", "1e999", "  ", "\n", "a" * 500]:
    try:
        r = evaluate(expr, SLOTS)
        state = "ok" if r.ok else f"拒绝({r.reason[:20]})"
    except Exception as e:
        state = f"❌ 抛异常 {type(e).__name__}"
    print(f"  {expr[:20]!r:26} → {state}")

print(f"\n结果：{ok} 通过 / {fail} 失败")
if fail:
    raise SystemExit(1)
