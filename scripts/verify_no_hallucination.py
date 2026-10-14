"""广度诊断：多少种问法会漏出「数据待核实」。

目的不是修单个问题，是找出所有泄漏路径。
"""

import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

from app.db import SessionLocal
from app.graph.workflow import run_chat

QUESTIONS = [
    # 无时长的运动类
    ("步行能消耗多少热量", "缺时长"),
    ("走路消耗多少", "缺时长"),
    ("快走能消耗多少", "缺时长"),
    ("运动能消耗多少", "缺时长"),
    ("慢跑消耗大吗", "定性"),
    # 有时长的（已修）
    ("快走30分钟消耗多少", "已修"),
    ("跑步40分钟消耗多少热量", "已修"),
    # 食物类
    ("西红柿里面含什么元素", "已修"),
    ("鸡蛋有什么营养成分", "已修"),
    ("请问吃完一个汉堡要跳多久的绳", "已修"),
    # 目标类
    ("我要减到正常范围需要减到多少公斤", "已修"),
    # 换算类
    ("100克米饭多少千卡", "已修"),
    # 未覆盖的换算
    ("一碗米饭等于多少运动", "未覆盖"),
    ("跑步能消耗掉一碗米饭吗", "未覆盖"),
    # 饮食建议
    ("减脂期每天应该吃多少千卡", "未覆盖"),
    ("我每天应该摄入多少蛋白质", "未覆盖"),
    # 对比
    ("米饭和面条哪个热量高", "未覆盖"),
    ("鸡蛋和牛奶哪个热量高", "未覆盖"),
    # 常识型（模型凭知识答，但含数字）
    ("每天应该喝多少水", "未覆盖"),
    ("正常人一天睡几小时", "不该拦"),
    ("蛋白质每天吃多少克合适", "未覆盖"),
]

db = SessionLocal()
leaked = []
try:
    for q, kind in QUESTIONS:
        try:
            r = run_chat(db, q)
            has = "待核实" in r.answer
            mark = "❌ 泄漏" if has else "✅"
            if has:
                leaked.append((q, kind))
            print(f"{mark} [{kind:<5}] {q}")
        except Exception as e:
            leaked.append((q, f"崩溃:{type(e).__name__}"))
            print(f"💥 [{kind:<5}] {q}  → {type(e).__name__}")
finally:
    db.close()

print(f"\n泄漏 {len(leaked)}/{len(QUESTIONS)}：")
for q, k in leaked:
    print(f"   {q}  ({k})")
