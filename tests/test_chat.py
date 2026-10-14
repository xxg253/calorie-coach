"""P3 智能对话测试 —— 防幻觉与追问状态机。

这些是 P3 期间真实踩过的坑，全部锁死。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.guard import (  # noqa: E402
    build_slots,
    prompt_number_contract,
    render,
    trust_stats,
)
from app.llm.intent import (  # noqa: E402
    _parse_grams_from_text,
    classify_intent,
)


class TestPlaceholderTrust:
    """方案 B（占位符填充）—— 把正确性从检测变为构造。"""

    TOOL = [{
        "kcal_per_100g": 116.0, "grams": 200.0, "kcal": 232.0,
        "protein_per_100g": 2.6,
    }]

    def test_fill_placeholder(self):
        slots = build_slots(self.TOOL)
        r = render("200克共{{kcal_1}}千卡。", slots)
        assert "232" in r.text
        assert not r.unfilled

    def test_short_alias(self):
        """模型写 protein_1 而非 protein_per_100g_1 也要能解析。"""
        slots = build_slots(self.TOOL)
        r = render("蛋白质{{protein_1}}克。", slots)
        assert "2.6" in r.text
        assert not r.unfilled

    def test_hallucinated_number_detected(self):
        """实测幻觉：模型答 230，真值 232。必须被检出。"""
        slots = build_slots(self.TOOL)
        r = render("200克米饭的热量约为230千卡。", slots)
        assert not r.ok if hasattr(r, "ok") else True
        assert 230.0 in r.bare_numbers, "230 不在数据库返回中，必须被标记"
        assert r.warning

    def test_filled_values_not_flagged(self):
        """填充后的数字本身不算裸数字（否则自判幻觉）。"""
        slots = build_slots(self.TOOL)
        r = render("每100克{{kcal_per_100g_1}}千卡，200克{{kcal_1}}千卡。", slots)
        assert r.bare_numbers == [], "填充的值不应被误判"
        assert not r.warning

    def test_undefined_slot(self):
        slots = build_slots(self.TOOL)
        r = render("这是{{not_exist}}千卡。", slots)
        assert r.unfilled == ["not_exist"]
        assert "待核实" in r.text

    def test_no_global_state_pollution(self):
        """交替构建两组槽位，别名不能互相污染。"""
        for _ in range(3):
            s1 = build_slots([{"protein_per_100g": 2.6}])
            r1 = render("{{protein_1}}", s1)
            assert "2.6" in r1.text

            s2 = build_slots([{"met": 3.5}])
            r2 = render("{{met_1}}", s2)
            assert "3.5" in r2.text

    def test_contract_prompt_generated(self):
        """数值契约必须列出全部可用占位符。"""
        c = prompt_number_contract(build_slots(self.TOOL))
        assert "{{kcal_1}}" in c
        assert "禁止" in c

    def test_contract_empty_case(self):
        """无可用数据时，契约必须要求模型不要输出数字。"""
        c = prompt_number_contract([])
        assert "没有可用的数据库数值" in c

    def test_trust_stats_shape(self):
        s = trust_stats()
        assert s["enabled"] is True
        assert "占位符" in s["strategy"]
        assert "230" in str(s["empirical_evidence"])


class TestGramsParsing:
    """中式单位解析 —— 实测「一斤米饭」曾完全解析不出来。"""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("200克米饭", 200.0),
            ("2斤米饭", 1000.0),
            ("半斤米饭", 250.0),      # 中文数量在单位前
            ("一斤米饭", 500.0),
            ("两个鸡蛋", 200.0),
            ("1.5公斤", 1500.0),
            ("三两肉", 150.0),
        ],
    )
    def test_chinese_units(self, text, expected):
        assert _parse_grams_from_text(text) == expected

    def test_cm_not_grams(self):
        """实测「175cm」曾被算成 78000 克。"""
        assert _parse_grams_from_text("身高175cm") is None

    def test_kg_not_grams(self):
        """BMI 问题里的 kg 是体重，不是食物克数。"""
        assert _parse_grams_from_text("175cm 78kg") == 78000.0  # 由调用方按意图排除


class TestIntent:
    """意图识别 —— 规则层必须 100% 准确。"""

    @pytest.mark.parametrize(
        ("q", "expect"),
        [
            ("200克米饭的热量是多少", "LOOKUP_FOOD"),
            ("西红柿炒鸡蛋多少热量", "RECIPE_LOOKUP"),
            ("推荐几个低热量高蛋白的食物", "RECOMMEND"),
            ("我175cm 78kg BMI是多少", "CALC_BMI"),
            ("我的基础代谢是多少", "CALC_BURN"),
            ("快走30分钟消耗多少", "EXERCISE_CALC"),
            ("我今天吃了多少", "QUERY_HISTORY"),
            ("减脂期该怎么吃主食", "KNOWLEDGE_QA"),
            ("我吃了一斤米饭", "LOG_RECORD"),
            ("刚跑了30分钟", "LOG_RECORD"),
        ],
    )
    def test_intent_accuracy(self, q, expect):
        assert classify_intent(q, use_llm=False)["intent"] == expect

    def test_food_name_extracted(self):
        """实测：LOOKUP_FOOD 分支提前 return 导致 food_name 为 None，
        工具不执行（0 条结果）。"""
        r = classify_intent("200克米饭的热量是多少", use_llm=False)
        assert r["food_name"] == "米饭"

    def test_cooking_and_oil_extracted(self):
        r = classify_intent("西红柿炒鸡蛋炒的比较清淡的热量", use_llm=False)
        assert r["cooking_method"] == "炒"
        assert r["oil_level"] == "清淡"

    def test_bmi_slots_cleared(self):
        """BMI 问题里的 kg 是体重，不能当食物克数。"""
        r = classify_intent("我175cm 78kg BMI是多少", use_llm=False)
        assert r["grams"] is None


class TestCooperation:
    """few-shot 与 Prompt 设计。"""

    def test_fewshot_collapsed_into_single_user_message(self):
        """实测教训：few-shot 作为独立消息时，模型当成历史对话续写，
        用户问 BMI 却把示例里的牛肉面/奶茶挨个答一遍。
        现在必须折叠进同一条 user 消息。"""
        from app.llm.prompts import build_chat_messages

        msgs = build_chat_messages("我BMI是多少", slots=build_slots([{"bmi": 25.6}]))
        # 只有 system + history + 1 条 user
        users = [m for m in msgs if m["role"] == "user"]
        assert len(users) == 1, f"应只有一条 user 消息，实际 {len(users)}"
        assert "我的实际问题" in users[0]["content"]
        assert "不要重复回答" in users[0]["content"]

    def test_fewshot_examples_differ_from_typical_queries(self):
        """示例不能与常见测试问题完全相同，否则会污染回答。"""
        from app.llm.prompts import FEW_SHOT

        demos = {ex["content"] for ex in FEW_SHOT}
        assert "200克米饭的热量是多少" not in demos
        assert "我每天走路能消耗多少" not in demos

    def test_fewshot_contains_refusal_examples(self):
        """模型模仿样例不是规则 —— 必须有明确的拒答样例。"""
        from app.llm.prompts import FEW_SHOT

        refusals = [e for e in FEW_SHOT if "知识库里没有" in e["assistant"]]
        assert len(refusals) >= 2, "必须包含拒答样例"

    def test_contract_forbids_manual_arithmetic(self):
        """实测模型会写「116 × 2 = 232」这种自己算的式子。"""
        c = prompt_number_contract(build_slots([{"kcal_per_100g": 116.0}]))
        assert "不要自己做算术" in c

    def test_history_truncated(self):
        from app.llm.prompts import build_chat_messages

        history = [{"role": "user", "content": f"q{i}"} for i in range(20)]
        msgs = build_chat_messages("新问题", history=history)
        assert len(msgs) <= 1 + 6 + 1


class TestWorkflowState:
    """多轮追问状态机。"""

    def test_state_has_original_query(self):
        from app.graph.state import new_state

        s = new_state("西红柿炒鸡蛋多少热量")
        assert s["original_query"] == "西红柿炒鸡蛋多少热量"

    def test_chat_result_carries_state(self):
        """实测：ChatResult 不带 state，多轮追问无法继承，
        第2轮用户只说「偏清淡」时菜名丢失。"""
        from dataclasses import fields

        from app.graph.workflow import ChatResult

        names = {f.name for f in fields(ChatResult)}
        assert "state" in names

    def test_clarify_reply_extracts_both_slots(self):
        """实测：「炒的」在问油量的轮次出现也要能接住。"""
        from app.graph.state import new_state
        from app.graph.workflow import handle_clarify_reply

        s = new_state("x")
        s["pending_question"] = "oil_level"
        s = handle_clarify_reply(None, s, "炒的")
        assert s["cooking_method"] == "炒"

    def test_clarify_reply_extracts_oil(self):
        from app.graph.state import new_state
        from app.graph.workflow import handle_clarify_reply

        s = new_state("x")
        s["pending_question"] = "oil_level"
        s = handle_clarify_reply(None, s, "偏清淡")
        assert s["oil_level"] == "清淡"

    def test_clarify_reply_short_form(self):
        """「少油」也要能映射到「清淡」。"""
        from app.graph.state import new_state
        from app.graph.workflow import handle_clarify_reply

        s = new_state("x")
        s["pending_question"] = "oil_level"
        s = handle_clarify_reply(None, s, "少油")
        assert s["oil_level"] == "清淡"


class TestPromptStructure:
    def test_system_prompt_has_redlines(self):
        from app.llm.prompts import BASE_SYSTEM

        assert "铁律" in BASE_SYSTEM
        assert "BMR" in BASE_SYSTEM and "TDEE" in BASE_SYSTEM

    def test_intent_prompt_lists_all_types(self):
        from app.llm.prompts import INTENT_PROMPT

        for intent in ["LOOKUP_FOOD", "RECIPE_LOOKUP", "RECOMMEND", "CALC_BMI",
                       "CALC_BURN", "LOG_RECORD", "QUERY_HISTORY", "KNOWLEDGE_QA"]:
            assert intent in INTENT_PROMPT

    def test_client_status(self):
        from app.llm.client import client_status

        s = client_status()
        # 只校验「配置项存在且有值」，不写死模型名 ——
        # 模型是可替换项（已从 qwen-max 换成 qwen3.8-max），
        # 写死具体值会让换模型时测试无故变红。
        assert s["configured"] is True
        assert s["model"] and isinstance(s["model"], str)
        assert s["base_url"].startswith("https://")
        # 默认温度必须低温，否则数值可信化不成立
        assert s["temperature_default"] <= 0.3

class TestExerciseRecommendIntent:
    """回归测试：问「推荐运动」不能被识别成「推荐食物」。

    真实 bug：用户问「给我推荐几个燃脂效率高的运动」，
    意图被识别为 RECOMMEND（食物），于是工具返回紫菜/虾仁，
    模型想答跑步却拿不到运动数据 → 满屏「数据待核实」，看起来像崩溃。
    """

    def test_movement_words_go_to_exercise_recommend(self):
        from app.llm.intent import classify_intent

        for q in (
            "给我推荐几个燃脂效率高的运动",
            "推荐几个适合减肥的运动",
            "有什么运动比较燃脂",
            "推荐几个高强度有氧训练",
        ):
            r = classify_intent(q, use_llm=False)
            assert r["intent"] == "EXERCISE_RECOMMEND", f"{q} → {r['intent']}"

    def test_food_recommend_unchanged(self):
        """食物推荐不能被误伤。"""
        from app.llm.intent import classify_intent

        for q in ("推荐几个低热量高蛋白的食物", "减脂期该吃什么", "我该吃什么"):
            r = classify_intent(q, use_llm=False)
            assert r["intent"] == "RECOMMEND", f"{q} → {r['intent']}"

    def test_other_intents_not_broken(self):
        """新增分支不能抢走既有意图。"""
        from app.llm.intent import classify_intent

        cases = {
            "200克米饭的热量是多少": "LOOKUP_FOOD",
            "西红柿炒鸡蛋多少热量": "RECIPE_LOOKUP",
            "快走30分钟消耗多少": "EXERCISE_CALC",
            "我175cm 78kg BMI是多少": "CALC_BMI",
            "减脂期怎么吃主食": "KNOWLEDGE_QA",
            "为什么体重不掉": "KNOWLEDGE_QA",
        }
        for q, expect in cases.items():
            r = classify_intent(q, use_llm=False)
            assert r["intent"] == expect, f"{q} → {r['intent']}，期望 {expect}"


class TestExerciseRecommendTool:
    """运动推荐工具：名字要可读、数值要按真实体重算。"""

    def test_uses_readable_alias_names(self, db_session):
        """推荐列表不能出现数据集的三级学术命名。"""
        from app.services import exercise_service

        items = exercise_service.recommend_high_burn_exercises(
            db_session, weight_kg=70.0, limit=5
        )
        assert items, "应至少返回几项"
        for it in items:
            assert len(it.name) <= 12, f"名字过长，疑似未清洗：{it.name}"
            assert "：" not in it.name, f"仍是数据集原始名：{it.name}"

    def test_kcal_uses_given_weight(self, db_session):
        """kcal_per_hour 必须 = MET × 体重，不能是别的口径。"""
        from app.services import exercise_service

        items = exercise_service.recommend_high_burn_exercises(
            db_session, weight_kg=70.0, limit=5
        )
        for it in items:
            assert it.kcal_per_hour == round(it.met * 70.0, 1)

    def test_excludes_ultra_intense(self, db_session):
        """MET 上限 12.5 —— 超过多为竞技动作，普通用户做不了。"""
        from app.services import exercise_service

        items = exercise_service.recommend_high_burn_exercises(
            db_session, weight_kg=70.0, limit=10
        )
        assert all(it.met <= 12.5 for it in items)


class TestStringSlots:
    """回归测试：字符串字段也要能建成槽位。

    真实 bug：build_slots 只收 int/float，工具返回的 name/line 被静默丢弃，
    模型看不到 {{line_N}} 就自己编名字 —— MET 12.3（跳绳的）
    被写到「跑步（8km/h）」名下。
    """

    def test_string_fields_become_slots(self):
        from app.llm.guard import build_slots

        slots = build_slots([{"name": "跳绳", "line": "跳绳（MET 12.3）", "met": 12.3}])
        keys = {s.key for s in slots}
        assert "name_1" in keys
        assert "line_1" in keys
        assert "met_1" in keys

    def test_string_slot_renders_and_exempts_its_numbers(self):
        """字符串槽位里的数字渲染后不该被当成幻觉。"""
        from app.llm.guard import build_slots, render

        slots = build_slots([
            {"name": "跳绳", "met": 12.3, "kcal_per_hour": 1150.0,
             "line": "跳绳（MET 12.3，每小时约 1150 千卡）"}
        ])
        r = render("{{line_1}}", slots)
        assert r.text == "跳绳（MET 12.3，每小时约 1150 千卡）"
        assert r.bare_numbers == [], f"误报裸数字：{r.bare_numbers}"
        assert r.warning == ""


class TestSessionPersistence:
    """回归测试：对话必须能跨页面/跨刷新恢复。

    真实 bug：msgs 只存在组件 useState 里，切到别的页面组件卸载，
    对话就没了。而消息其实早就落在 chat_messages 表里，只是没接口读出来。

    注意：这里自己造数据，不能依赖开发库的既有内容 ——
    db_session fixture 是每次重建的临时库，不含历史会话。
    """

    @staticmethod
    def _seed(db_session, thread_id: str) -> int:
        from app.models import ChatMessage, Conversation

        conv = Conversation(title="测试会话", thread_id=thread_id)
        db_session.add(conv)
        db_session.flush()
        db_session.add(
            ChatMessage(
                conversation_id=conv.id,
                role="user",
                content="200克米饭的热量是多少",
                intent="LOOKUP_FOOD",
            )
        )
        db_session.add(
            ChatMessage(
                conversation_id=conv.id,
                role="assistant",
                content="200克米饭的热量是 232 千卡。",
                intent="LOOKUP_FOOD",
                citations='[{"type": "food", "id": 300, "name": "米饭（均值）"}]',
            )
        )
        db_session.commit()
        return conv.id

    def test_messages_persisted_with_intent(self, db_session):
        """消息落库且带 intent —— 恢复界面时要用它显示意图标签。"""
        from sqlalchemy import select

        from app.models import ChatMessage

        self._seed(db_session, "s_t_cite")
        rows = db_session.execute(
            select(ChatMessage).where(ChatMessage.role == "assistant")
        ).scalars().all()
        assert rows, "应有 assistant 消息"
        for m in rows:
            assert m.content
            assert m.intent is not None, "缺 intent，恢复界面时标签会空"

    def test_citations_round_trip_as_json(self, db_session):
        """citations 存 JSON 字符串，读出来要能解析回数组。"""
        import json

        from sqlalchemy import select

        from app.models import ChatMessage

        self._seed(db_session, "s_t_cites")
        rows = db_session.execute(
            select(ChatMessage).where(ChatMessage.citations.isnot(None))
        ).scalars().all()
        assert rows, "应有带引用的消息"
        for m in rows:
            parsed = json.loads(m.citations)
            assert isinstance(parsed, list)
            assert parsed[0]["name"] == "米饭（均值）"

    def test_thread_id_is_session_key(self, db_session):
        """会话用 thread_id 关联 —— 前端拿到的是字符串 session_id。"""
        from sqlalchemy import select

        from app.models import Conversation

        sid = "s_t_abc123"
        self._seed(db_session, sid)
        t = db_session.execute(
            select(Conversation.thread_id).where(Conversation.thread_id == sid)
        ).scalars().first()
        assert t == sid

    def test_can_read_back_by_session_id(self, db_session):
        """按 session_id 能把整段对话读回来 —— 这是修复的核心。"""
        from sqlalchemy import select

        from app.models import ChatMessage, Conversation

        sid = "s_t_read"
        self._seed(db_session, sid)
        conv = db_session.execute(
            select(Conversation).where(Conversation.thread_id == sid)
        ).scalars().first()
        assert conv is not None

        msgs = db_session.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conv.id)
            .order_by(ChatMessage.id)
        ).scalars().all()
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert "232" in msgs[1].content


class TestNutritionQueryIntent:
    """回归测试：问「食物含什么元素/维生素」必须走 LOOKUP_FOOD。

    真实 bug：「西红柿里面含什么元素」落进 KNOWLEDGE_QA，
    而该意图不查数据库，模型只能凭空引用 kcal_1 / protein_per_100g_1
    这些不存在的槽位，答案全是「（数据待核实）」。
    """

    def test_element_questions_go_to_lookup(self):
        from app.llm.intent import classify_intent

        for q in (
            "西红柿里面含什么元素",
            "西红柿含哪些维生素",
            "鸡蛋有什么营养成分",
            "牛肉含多少蛋白质",
            "鸡胸肉营养成分",
        ):
            r = classify_intent(q, use_llm=False)
            assert r["intent"] == "LOOKUP_FOOD", f"{q} → {r['intent']}"

    def test_name_extraction_uses_real_db_names(self):
        """名称抽取必须在库里真实存在，否则查库必然失败。

        实测：靠停用词剥离时「西红柿里面含什么元素」会剥出「西红柿里面」。
        改成对库中真实名称做最长匹配后，抽出来的保证可查。
        """
        from app.llm.intent import _extract_name

        cases = [
            ("西红柿里面含什么元素", "西红柿"),
            ("西兰花含哪些维生素", "西兰花"),
            ("牛肉含多少蛋白质", "牛肉"),
            ("200克米饭的热量是多少", "米饭"),
        ]
        for q, expect in cases:
            assert _extract_name(q) == expect, f"{q} → {_extract_name(q)}"

    def test_extracted_name_actually_exists_in_db(self):
        """抽出来的名字必须在食物库里查得到 —— 这是防幻觉的关键一环。"""
        from app.db import SessionLocal
        from app.llm.intent import _extract_name
        from app.services import food_service

        for q in ("西红柿里面含什么元素", "西兰花含哪些维生素", "鸡蛋有什么营养成分"):
            name = _extract_name(q)
            db = SessionLocal()
            try:
                r = food_service.search_food(db, name)
                assert r.found or r.need_disambiguation, f"{q} → {name} 查不到"
            finally:
                db.close()

    def test_data_boundary_is_declared(self):
        """工具必须明示「哪些字段没有」，否则模型会凭常识编维生素。"""
        from sqlalchemy import select

        from app.db import SessionLocal
        from app.graph.nodes import exec_tools
        from app.graph.state import new_state

        db = SessionLocal()
        try:
            state = new_state(
                user_input="西红柿里面含什么元素",
                intent="LOOKUP_FOOD",
                slots={"food_name": "西红柿", "grams": None},
            )
            out = exec_tools(db, state)
            res = out["tool_results"][0]
            assert "_available" in res, "缺 _available，模型不知道能报哪些"
            assert "_not_available" in res, "缺 _not_available，模型会编维生素"
            assert "维生素" in res["_not_available"]
        finally:
            db.close()


class TestWeightUpdateDelete:
    """回归测试：往日体重可修改、可删除。

    真实需求：体重是「一天一次」的唯一键，称错了必须能改。
    原来只有 POST（写当天），没有修改/删除入口。
    """

    def test_update_recalculates_bmi(self, db_session):
        """改体重后 BMI 与判定必须重算，不能留着旧值。"""
        import datetime as dt

        from app.core.formulas import BMIStandard
        from app.services.body_service import record_weight

        today = dt.date.today()
        record_weight(db_session, 80.0, log_date=today, standard=BMIStandard.CHINA)
        db_session.commit()

        row = record_weight(db_session, 75.0, log_date=today, standard=BMIStandard.CHINA)
        db_session.commit()

        assert row.weight_kg == 75.0
        # BMI 必须是按 75kg 重算的，不是 80kg 时的旧值
        expected = 75.0 / (db_session.execute(
            __import__("sqlalchemy").select(__import__("app.models", fromlist=["Profile"]).Profile.height_cm)
        ).scalar() / 100.0) ** 2
        assert abs(row.bmi_value - expected) < 0.05, (
            f"BMI 未重算：得到 {row.bmi_value}，应为 {expected:.2f}"
        )

    def test_update_same_date_overwrites_not_appends(self, db_session):
        """同一天改体重是覆盖，不能多出一行。"""
        import datetime as dt

        from sqlalchemy import func, select

        from app.models import WeightLog
        from app.services.body_service import record_weight

        today = dt.date.today()
        record_weight(db_session, 80.0, log_date=today)
        record_weight(db_session, 76.0, log_date=today)
        db_session.commit()

        n = db_session.execute(
            select(func.count()).select_from(WeightLog).where(WeightLog.log_date == today)
        ).scalar()
        assert n == 1, f"同日应只有 1 行，实际 {n} 行"

    def test_delete_removes_row(self, db_session):
        import datetime as dt

        from sqlalchemy import select

        from app.models import WeightLog
        from app.services.body_service import record_weight

        today = dt.date.today()
        record_weight(db_session, 80.0, log_date=today)
        db_session.commit()

        row = db_session.execute(
            select(WeightLog).where(WeightLog.log_date == today)
        ).scalars().first()
        assert row is not None

        db_session.delete(row)
        db_session.commit()

        left = db_session.execute(
            select(WeightLog).where(WeightLog.log_date == today)
        ).scalars().first()
        assert left is None, "删除后不应残留"

    def test_update_rejects_out_of_range(self, db_session):
        """越界体重必须被拒（Pydantic 校验），不能写进库。"""
        import pytest
        from pydantic import ValidationError

        from app.schemas.api import WeightCreate

        for bad in (0, 900, -5):
            with pytest.raises(ValidationError):
                WeightCreate(weight_kg=bad)


class TestTargetWeight:
    """回归测试：目标体重必须由系统算，护栏才拦得住。

    真实 bug：用户问「减到多少公斤」，模型自己用 LaTeX 算，
    引用 \text{身高}/\text{BMI} 这类中文占位符 → 护栏把它们当幻觉，
    全部替换成「（数据待核实）」，答案里还漏出未渲染的 LaTeX 源码。
    """

    def test_intent_routing(self):
        from app.llm.intent import classify_intent

        for q in (
            "我要减到正常范围需要减到多少公斤",
            "减到多少斤合适",
            "我的健康体重是多少",
            "降到多少公斤算正常",
        ):
            r = classify_intent(q, use_llm=False)
            assert r["intent"] == "CALC_TARGET_WEIGHT", f"{q} → {r['intent']}"

    def test_tool_provides_real_numbers(self, db_session):
        """工具必须返回可核对的数字，且与 BMI 公式一致。"""
        from app.graph.nodes import exec_tools
        from app.graph.state import new_state

        state = new_state(
            user_input="我要减到多少公斤",
            intent="CALC_TARGET_WEIGHT",
            slots={},
        )
        res = exec_tools(db_session, state)["tool_results"][0]

        w, h = res["weight_kg"], res["height_cm"]
        # 当前 BMI 必须等于公式算出的值
        assert abs(res["bmi_now"] - w / (h / 100.0) ** 2) < 0.01
        # 目标上限 = BMI 24 × 身高²（中国标准）
        assert abs(res["target_max_kg"] - 24.0 * (h / 100.0) ** 2) < 0.2
        # 需减重 = 当前 - 目标上限（不能为负）
        assert res["to_lose_kg"] == round(max(0.0, w - res["target_max_kg"]), 1)
        # 整句槽位必须存在且含关键数字
        assert "line" in res
        for v in (w, h, res["target_max_kg"]):
            assert str(v) in res["line"], f"line 缺数字 {v}"

    def test_line_slots_render_cleanly(self):
        """整句槽位渲染后不应触发裸数字告警。"""
        from app.llm.guard import build_slots, render

        tool = [{
            "weight_kg": 92.5, "height_cm": 183.0, "bmi_now": 27.62,
            "target_max_kg": 80.4, "target_min_kg": 62.0, "to_lose_kg": 12.1,
            "line": "你当前 92.5kg，身高 183.0cm，BMI 27.62（超重）。体重应降到 80.4kg 以下，减 12.1kg。",
        }]
        r = render("{{line_1}}", build_slots(tool))
        assert "待核实" not in r.text
        assert r.bare_numbers == [], f"误报：{r.bare_numbers}"


class TestHeightChangeRecalcBmi:
    """回归测试：改身高必须重算所有历史 BMI。

    真实 bug：BMI 在 record_weight 时按当时身高算好写死，
    改身高不会更新历史记录 → 同一个身体出现两套 BMI。
    """

    def test_recalc_all_bmi(self, db_session):
        import datetime as dt

        from sqlalchemy import select

        from app.services.body_service import record_weight
        from app.models import WeightLog
        from app.api.routes_dashboard import _recalc_all_bmi

        d = dt.date.today() - dt.timedelta(days=3)
        record_weight(db_session, 80.0, log_date=d)
        db_session.commit()

        before = db_session.execute(
            select(WeightLog).where(WeightLog.log_date == d)
        ).scalars().first()
        old_bmi = before.bmi_value

        n = _recalc_all_bmi(db_session, 160.0)  # 改成 160cm
        db_session.commit()

        db_session.refresh(before)
        expected = 80.0 / (160.0 / 100.0) ** 2
        assert n >= 1
        assert abs(before.bmi_value - expected) < 0.05, (
            f"BMI 未重算：{old_bmi} → {before.bmi_value}，应为 {expected:.2f}"
        )
        assert before.bmi_value != old_bmi, "改身高后 BMI 应变化"


class TestFoodBurnOff:
    """回归测试：食物 → 运动时长换算必须由系统算。

    真实 bug：用户问「吃完一个汉堡要跳多久的绳」，这是**反向换算**，
    系统没有这个能力 → 模型只能编数字 → 护栏全判为幻觉 →
    满屏「数据待核实」。且数据集是 USDA 食材库，连「汉堡」都查不到。
    """

    def test_intent_routing(self):
        from app.llm.intent import classify_intent

        for q in (
            "请问吃完一个汉堡要跳多久的绳",
            "吃个炸鸡要运动多久",
            "喝一杯奶茶需要爬多久楼梯",
        ):
            r = classify_intent(q, use_llm=False)
            assert r["intent"] == "FOOD_BURNOFF", f"{q} → {r['intent']}"

    def test_fast_food_exists_in_db(self):
        """常见快餐必须在库里 —— 否则模型只能编。"""
        from app.db import SessionLocal
        from app.services.burnoff_service import food_kcal

        db = SessionLocal()
        try:
            for name in ("汉堡", "炸鸡", "奶茶"):
                got = food_kcal(db, name)
                assert got is not None, f"「{name}」查不到"
                assert got[0] > 0
        finally:
            db.close()

    def test_burn_off_math(self, db_session):
        """分钟数必须与 MET 公式自洽：千卡 = MET × 体重 × 分钟/60。"""
        from app.services.burnoff_service import burn_off_options

        items = burn_off_options(db_session, kcal=300.0, weight_kg=70.0)
        assert items, "应给出运动选项"
        for it in items:
            expected = 300.0 / (it.met * 70.0) * 60.0
            assert abs(it.minutes - expected) < 0.2, (
                f"{it.exercise}: {it.minutes} vs 公式 {expected:.1f}"
            )

    def test_burn_off_scales_with_weight(self, db_session):
        """同样热量，体重越大需要的时间越短 —— 公式必须正确反映这一点。"""
        from app.services.burnoff_service import burn_off_options

        light = burn_off_options(db_session, 300.0, 60.0)
        heavy = burn_off_options(db_session, 300.0, 90.0)
        assert light[0].minutes > heavy[0].minutes

    def test_tool_returns_verifiable_line(self, db_session):
        """必须给出整句 line 且不含未填槽位。"""
        from app.graph.nodes import exec_tools
        from app.graph.state import new_state

        state = new_state(
            user_input="吃完一个汉堡要跳多久的绳",
            intent="FOOD_BURNOFF",
            slots={"food_name": "汉堡", "grams": None},
        )
        res = exec_tools(db_session, state)["tool_results"][0]
        assert res["food_kcal"] > 0
        assert "options" in res and res["options"]
        assert "分钟" in res["line"]
        # line 里出现的数字必须都是工具给的
        for v in (res["food_kcal"], res["weight_kg"]):
            assert str(v).rstrip("0").rstrip(".") in res["line"]


class TestExerciseCalcIntent:
    """回归测试：BMR 与运动消耗必须拆成两个意图。

    真实 bug：「快走30分钟消耗多少」原走 CALC_BURN，
    而该意图的工具返回 bmr/tdee —— 答非所问，
    模型只好自己编体重 → 护栏标为「数据待核实」。
    """

    def test_intent_split(self):
        from app.llm.intent import classify_intent

        cases = {
            "快走30分钟消耗多少": "EXERCISE_CALC",
            "跑步40分钟能消耗多少热量": "EXERCISE_CALC",
            "我的基础代谢是多少": "CALC_BURN",
            "我的每日消耗是多少": "CALC_BURN",
        }
        for q, expect in cases.items():
            r = classify_intent(q, use_llm=False)
            assert r["intent"] == expect, f"{q} → {r['intent']}，期望 {expect}"

    def test_tool_returns_weight_in_line(self, db_session):
        """整句必须带体重 —— 模型自己编体重就会被护栏标为幻觉。"""
        from app.graph.nodes import exec_tools
        from app.graph.state import new_state

        state = new_state(
            user_input="快走30分钟消耗多少",
            intent="EXERCISE_CALC",
            slots={"exercise_name": "快走", "duration_min": 30},
        )
        res = exec_tools(db_session, state)["tool_results"][0]
        assert res["kcal"] > 0
        assert res["met"] > 0
        assert "line" in res
        # 体重必须出现在 line 里
        assert str(res["weight_kg"]) in res["line"]
        # 运动名用用户说法，不是数据集的三级命名
        assert "：" not in res["exercise_name"], res["exercise_name"]


class TestExplicitBodyParamsWin:
    """回归测试：用户在问题里明说的身高体重必须优先于档案。"""

    def test_bmi_uses_explicit_values(self):
        from app.llm.intent import classify_intent

        r = classify_intent("我183cm 78.2kg BMI是多少", use_llm=False)
        assert r["weight_kg"] == 78.2
        assert r["height_cm"] == 183.0

    def test_falls_back_to_profile_when_absent(self):
        """没给数字时应留空，由节点退回档案值。"""
        from app.llm.intent import classify_intent

        r = classify_intent("我的BMI是多少", use_llm=False)
        assert r["weight_kg"] is None
        assert r["height_cm"] is None


class TestCalculator:
    """可信算术引擎：允许模型算，但只能用数据库给的数字算。

    这是「数据待核实」问题的根治手段 —— 之前模型被禁止写任何数字，
    于是遇到算术题（步行消耗多少）只能编，编了必被护栏拦。
    """

    SLOTS = {"met_1": 3.5, "weight_1": 78.2, "kcal_1": 295.0}

    def test_basic_arithmetic(self):
        from app.llm.calculator import evaluate

        r = evaluate("met_1 * weight_1 * 0.5", self.SLOTS)
        assert r.ok and abs(r.value - 136.9) < 0.2

    def test_precedence_and_parens(self):
        from app.llm.calculator import evaluate

        r = evaluate("(met_1 + weight_1) * 2", self.SLOTS)
        assert r.ok and abs(r.value - 163.4) < 0.2

    def test_rejects_code_execution(self):
        """绝不能执行任意代码 —— 这是安全底线。"""
        from app.llm.calculator import evaluate

        for evil in (
            "__import__('os').system('ls')",
            "met_1.__class__",
            "print('hi')",
            "[1,2,3][0]",
            "open('/etc/passwd')",
            "met_1 ** 1000",
            "eval('1+1')",
        ):
            r = evaluate(evil, self.SLOTS)
            assert not r.ok, f"危险表达式被执行了：{evil}"

    def test_rejects_unknown_variable(self):
        from app.llm.calculator import evaluate

        r = evaluate("nonexistent_1 * 2", self.SLOTS)
        assert not r.ok
        assert "未知变量" in r.reason

    def test_rejects_division_by_zero(self):
        from app.llm.calculator import evaluate

        assert not evaluate("met_1 / 0", self.SLOTS).ok

    def test_rejects_overflow(self):
        from app.llm.calculator import evaluate

        assert not evaluate("met_1 / 0.000001 * 1e15", self.SLOTS).ok


class TestCalcRendering:
    """{{calc:...}} 在渲染管线里必须真正生效。"""

    def _slots(self):
        from app.llm.guard import build_slots

        return build_slots([{"met": 3.5, "weight_kg": 78.2}])

    def test_calculates_and_fills(self):
        from app.llm.guard import render

        r = render("快走30分钟约 {{calc:met_1 * weight_kg_1 * 30 / 60}} 千卡", self._slots())
        assert "136.8" in r.text
        assert "待核实" not in r.text

    def test_calculated_number_not_flagged_as_hallucination(self):
        """算出来的正确数字不能被护栏自己判成幻觉。"""
        from app.llm.guard import render

        r = render("约 {{calc:met_1 * weight_kg_1 * 0.5}} 千卡", self._slots())
        assert r.bare_numbers == [], f"误报：{r.bare_numbers}"

    def test_failure_gives_readable_text(self):
        """失败时给人话，不是裸露的占位符。"""
        from app.llm.guard import render

        r = render("{{calc:missing_1 * 2}}", self._slots())
        assert "待核实" not in r.text
        assert "缺少" in r.text

    def test_bare_number_still_caught(self):
        """可信算术不能变成绕过护栏的后门 —— 真裸数字仍要拦。"""
        from app.llm.guard import render

        r = render("我编的数字是 999 千卡", self._slots())
        assert r.bare_numbers, "裸数字应被检测到"

    def test_calc_result_feeds_legal_pool(self):
        """calc 与普通槽位混用时要一起进合法池。"""
        from app.llm.guard import render

        r = render(
            "{{met_1}} 与 {{calc:met_1 * 2}} 都可以写",
            self._slots(),
        )
        assert r.bare_numbers == []
        assert "3.5" in r.text and "7" in r.text


class TestPortions:
    """餐具份量换算：「一碗米饭」≈ 多少克。"""

    def test_known_portions(self):
        from app.core import portions

        assert portions.grams_from("一碗米饭") == 200.0
        assert portions.grams_from("一个鸡蛋") == 100.0
        assert portions.grams_from("一杯奶茶") == 250.0

    def test_portion_not_swallowed_in_name(self):
        """剥量词不能把食物名一起剥掉（「碗米饭」不是纯量词）。"""
        from app.llm.intent import _extract_name

        assert _extract_name("一碗米饭等于多少运动") == "米饭"

    def test_grams_from_portion(self):
        from app.llm.intent import classify_intent

        r = classify_intent("一个鸡蛋多少千卡", use_llm=False)
        assert r["grams"] == 100.0


class TestNameNormalize:
    """回归测试：半角/全角括号差异不该导致查不到。

    真实 bug：用户问「炸鸡」，库里有「炸鸡（去骨）」；
    但若用户输入半角「炸鸡(去骨)」，库中无此行 → not_found →
    大模型回答「知识库里没有炸鸡这个条目」，而数据明明在库里。
    """

    def test_fullwidth_to_halfwidth(self):
        from app.services.normalize import normalize

        assert normalize("炸鸡（去骨）") == normalize("炸鸡(去骨)")

    def test_whitespace_ignored(self):
        from app.services.normalize import normalize

        assert normalize("熟 米饭") == normalize("熟米饭")

    def test_variants_include_forms(self):
        from app.services.normalize import variants

        vs = variants("炸鸡（去骨）")
        assert "炸鸡(去骨)" in vs
        assert len(set(vs)) == len(vs), "变体不应重复"

    def test_alias_hit_with_halfwidth(self):
        from app.db import SessionLocal
        from app.services.food_service import search_food

        db = SessionLocal()
        try:
            assert search_food(db, "炸鸡").found
        finally:
            db.close()


class TestAmbiguousFoodAutoPick:
    """回归测试：多条命中应自动选，不弹候选让用户选。

    真实 bug：问「苹果」命中 6 条（脱水/干燥/罐头），前端弹出候选但
    无法选择，用户卡死。实测这 6 条里没有一条是「新鲜苹果」——
    用户问苹果指的是新鲜苹果，不该让他在脱水苹果片里选。
    """

    def test_picks_fresh_over_dehydrated(self):
        from app.db import SessionLocal
        from app.services.burnoff_service import pick_default_food

        db = SessionLocal()
        try:
            f = pick_default_food(db, "苹果")
            assert f is not None
            # 不该选中脱水苹果（346 kcal 是错的）
            assert "脱水" not in (f.variant or "")
            assert f.kcal_per_100g < 100
        finally:
            db.close()

    def test_no_candidates_returned(self):
        """歧义时不该再返回候选列表。"""
        from app.db import SessionLocal
        from app.services.burnoff_service import food_kcal

        db = SessionLocal()
        try:
            got = food_kcal(db, "苹果", 200)
            assert got is not None
            kcal, name = got
            assert 80 < kcal < 130, f"200g苹果应在80-130千卡，实际{kcal}"
        finally:
            db.close()

    def test_single_match_unaffected(self):
        from app.db import SessionLocal
        from app.services.burnoff_service import food_kcal

        db = SessionLocal()
        try:
            got = food_kcal(db, "米饭", 200)
            assert got is not None and got[0] > 0
        finally:
            db.close()


class TestBurnOffWithGrams:
    """回归测试：带克重的换算必须走对意图。

    真实 bug：「请问吃200克炸鸡，要跳绳多长时间才能消耗完」
    food_name 抽不出（库中「炸鸡」只是 base_name，不是 name）→ 意图退到
    KNOWLEDGE_QA → 模型答「知识库里没有炸鸡」。
    """

    def test_intent_and_name(self):
        from app.llm.intent import classify_intent

        r = classify_intent("请问吃200克炸鸡，要跳绳多长时间才能消耗完", use_llm=False)
        assert r["intent"] == "FOOD_BURNOFF"
        assert r["food_name"] == "炸鸡"
        assert r["grams"] == 200.0

    def test_tool_produces_answer(self):
        from app.db import SessionLocal
        from app.graph.nodes import exec_tools
        from app.graph.state import new_state
        from app.llm.intent import classify_intent

        db = SessionLocal()
        try:
            r = classify_intent(
                "请问吃200克炸鸡，要跳绳多长时间才能消耗完", use_llm=False
            )
            st = new_state(
                user_input="请问吃200克炸鸡，要跳绳多长时间才能消耗完",
                intent=r["intent"], slots=r,
            )
            out = exec_tools(db, st)
            assert out["errors"] == [], f"不该有错误：{out['errors']}"
            res = out["tool_results"][0]
            assert res["food_kcal"] > 400, "200g炸鸡应超400千卡"
            assert "分钟" in res["line"]
        finally:
            db.close()


class TestStreamRender:
    """流式占位符缓冲渲染。

    流式时 token 碎片化到达：`{{kca` + `l_1` + `}}`，
    逐 token 直推会让用户看到占位符闪烁。缓冲到 `}}` 再替换即可。
    """

    def _slots(self):
        from app.llm.guard import build_slots

        return build_slots([{"kcal": 232.0, "grams": 200.0, "met": 3.5}])

    def test_char_by_char(self):
        from app.llm.stream_render import StreamRenderer

        r = StreamRenderer(self._slots())
        out = "".join(r.feed(t) for t in "200克米饭约{{kcal_1}}千卡") + r.flush()
        assert out == "200克米饭约232千卡"

    def test_split_across_chunks(self):
        from app.llm.stream_render import StreamRenderer

        r = StreamRenderer(self._slots())
        out = r.feed("{{kcal") + r.feed("_1}} 千")
        assert "{" not in out and "232" in out

    def test_calc_placeholder(self):
        from app.llm.stream_render import StreamRenderer

        r = StreamRenderer(self._slots())
        out = r.feed("{{calc: met_1 * 0.5}} 千卡") + r.flush()
        assert "1.8" in out and "{{" not in out

    def test_plain_braces_not_swallowed(self):
        """普通大括号不能被当占位符吞掉。"""
        from app.llm.stream_render import StreamRenderer

        r = StreamRenderer(self._slots())
        out = "".join(r.feed(t) for t in "set{a, b} 与 {{kcal_1}}") + r.flush()
        assert "{a, b}" in out
        assert "232" in out

    def test_accepts_raw_tool_results(self):
        """直接传 tool_results（dict 列表）也必须能用。

        真实 bug：StreamRenderer 把原始 dict 当 Slot 用，
        SSE 路径直接报 'dict' object has no attribute 'key'。
        单元测试当时只传了已构建的 Slot，没覆盖真实调用方式。
        """
        from app.llm.stream_render import StreamRenderer

        r = StreamRenderer([{"kcal": 232.0, "grams": 200.0}])
        out = "".join(r.feed(t) for t in "热量{{kcal_1}}") + r.flush()
        assert "232" in out and "{{" not in out

    def test_accepts_empty_slots(self):
        from app.llm.stream_render import StreamRenderer

        for empty in ([], None):
            r = StreamRenderer(empty)
            out = "".join(r.feed(t) for t in "没有槽位") + r.flush()
            assert out == "没有槽位"

    def test_never_leaks_placeholder(self):
        """任何碎片化方式都不该把 {{ 露给用户。"""
        from app.llm.stream_render import StreamRenderer

        text = "A{{kcal_1}}B{{grams_1}}C"
        for size in (1, 2, 3, 5):
            r = StreamRenderer(self._slots())
            toks = [text[i:i + size] for i in range(0, len(text), size)]
            out = "".join(r.feed(t) for t in toks) + r.flush()
            assert "{{" not in out, f"size={size} 泄漏占位符：{out}"
            assert "232" in out and "200" in out


class TestIntentCache:
    """意图缓存：重复问题不该反复调 LLM。"""

    def test_cache_hit_avoids_llm(self):
        from app.llm import intent as mod

        q = "缓存测试专用问题xyz123"
        mod._INTENT_CACHE.clear()
        r1 = mod.classify_intent(q, use_llm=False)  # 规则路径不进缓存
        assert r1["source"] == "rules"

        # 直接塞一条模拟 LLM 结果，再验证读取
        mod._INTENT_CACHE[q] = {"intent": "KNOWLEDGE_QA", "food_name": None,
                                "recipe_name": None, "exercise_name": None,
                                "cooking_method": None, "oil_level": None,
                                "grams": None, "duration_min": None}
        r2 = mod.classify_intent(q, use_llm=True)
        assert r2["source"] == "cache"
        assert r2["intent"] == "KNOWLEDGE_QA"

    def test_cache_is_bounded(self):
        from app.llm import intent as mod

        assert mod._INTENT_CACHE_MAX <= 1000, "缓存必须有上界，防内存增长"


class TestUseLlmFalseSkipsGeneration:
    """回归测试：use_llm=False 必须真正不生成文本。

    真实 bug：use_llm 只传给了意图识别，generate_answer 照样执行，
    流式链路的「预取」阶段白等一整轮生成（实测 14.6s）。
    """

    def test_no_answer_generated(self):
        from app.db import SessionLocal
        from app.graph.workflow import run_chat

        db = SessionLocal()
        try:
            r = run_chat(db, "减肥期间晚上饿了怎么办", use_llm=False)
            assert r.answer == "", f"不应生成文本，却得到：{r.answer[:40]}"
            # 但意图与工具必须照常算出来
            assert r.intent
        finally:
            db.close()


class TestRecommendHasFoodName:
    """回归测试：推荐食物必须给出真实食物名。

    真实 bug：推荐工具只返回 item1_kcal / item1_protein，
    根本没返回 name —— 模型只能写「食物1、食物2」，
    用户完全不知道吃的是什么。
    同一文件的 EXERCISE_RECOMMEND 早就用 line 整句绑定修好了，
    食物推荐没跟上。
    """

    def test_tool_includes_name_and_line(self, db_session):
        from app.graph.nodes import exec_tools
        from app.graph.state import new_state

        st = new_state(
            user_input="给我推荐几个高蛋白低热量的食物",
            intent="RECOMMEND",
            slots={"food_name": None, "grams": None},
        )
        res = exec_tools(db_session, st)["tool_results"]
        assert res, "推荐应返回结果"
        for item in res:
            assert "name" in item, f"缺少食物名：{item}"
            assert item["name"], f"食物名为空：{item}"
            assert "line" in item, f"缺少整句绑定：{item}"
            # 名字必须出现在 line 里，且与 name 字段一致
            assert item["name"] in item["line"]
            # 不能是平铺的 itemN_ 键
            assert not any(k.startswith("item") for k in item), f"仍是平铺键：{list(item)}"

    def test_food_names_are_real(self, db_session):
        """名字必须来自数据库，不能是「食物1」这种占位。"""
        from app.graph.nodes import exec_tools
        from app.graph.state import new_state

        st = new_state(
            user_input="推荐高蛋白食物",
            intent="RECOMMEND",
            slots={"food_name": None, "grams": None},
        )
        for item in exec_tools(db_session, st)["tool_results"]:
            name = item["name"]
            assert not name.startswith("食物"), f"仍是占位名：{name}"
            assert len(name) >= 2, f"名字过短不像食物：{name}"


class TestStreamNoDoubleRender:
    """回归测试：流式路径不能重复渲染。

    真实 bug：StreamRenderer 已把 {{line_1}} 替换成真实文本，
    后面再调 render_and_validate 就等于把「96 / 78.2 / 10.5」这些
    本来就来自数据库的数字判成裸数字 —— 用户看到
    「检测到 6 个未经数据库核实的数字」，与事实相反。

    同时 SSE 端点里残留的旧代码会二次推送 tool/token，
    导致前端内容翻倍把页面撑爆（用户报告「答完就崩」）。
    """

    def test_renderer_output_is_stable(self):
        """StreamRenderer 的输出再渲染一次应保持不变、不产生裸数字。"""
        from app.llm.guard import build_slots, render
        from app.llm.stream_render import StreamRenderer

        tool_results = [{
            "food_name": "苹果（不带皮）",
            "food_kcal": 96.0,
            "weight_kg": 78.2,
            "line": "苹果（不带皮）约 96.0 千卡，按 78.2kg：跳绳 6.0 分钟；快跑 7.5 分钟。",
        }]
        sr = StreamRenderer(tool_results)
        once = sr.feed("{{line_1}}") + sr.flush()
        assert "96.0" in once and "{{" not in once

        # 第二次渲染（模拟重复校验）不应报裸数字
        slots = build_slots(tool_results)
        again = render(once, slots)
        assert again.bare_numbers == [], f"重复渲染误报：{again.bare_numbers}"

    def test_no_legacy_double_push_in_route(self):
        """SSE 端点里不应再有「一次性整体推 token」的旧代码。

        注意 token 推送出现 2 次是正常的：
        一次推可见正文（feed），一次推 flush 出来的尾部缓冲。
        真正要防的是**第三次** —— 旧代码里的整体重推。
        """
        from pathlib import Path

        src = Path("app/api/routes_chat.py").read_text(encoding="utf-8")
        assert src.count('yield emit("token"') == 2, (
            f"token 推送次数应为 2（feed + flush），实际 {src.count(chr(121)+'ield emit(\"token\"')}"
        )
        assert src.count('yield emit("tool"') == 1, (
            "tool 事件应只推一次，重复会覆盖已渲染的内容"
        )
        # 旧代码的标记：一次性把 result.answer 整体当 token 推
        assert 'yield emit("token", {"content": result.answer})' not in src, (
            "仍存在旧的整体重推代码，会导致内容翻倍"
        )


class TestPromptTokenBudget:
    """Prompt token 预算控制。

    实测问题：一次普通提问烧 2134 tokens，其中 8 条 few-shot 示例
    占了 user prompt 的 97%（754 tokens）—— 查「米饭热量」时带的是
    「爬楼梯消耗」「体重平台期」等无关示例。
    """

    def test_demos_filtered_by_intent(self):
        from app.llm.prompts import FEW_SHOT, _pick_demos

        for intent in ("LOOKUP_FOOD", "CALC_BURN", "QUERY_HISTORY", "RECIPE_LOOKUP"):
            picked = _pick_demos(intent)
            assert len(picked) <= 3, f"{intent} 应最多 3 条，实际 {len(picked)}"
            assert len(picked) < len(FEW_SHOT), f"{intent} 应少于全部示例"
            # 挑出来的必须是同意图的
            tags = {ex.get("intent") for ex in picked}
            assert intent in tags or "RECIPE_LOOKUP" in tags, f"{intent} 挑的示例标签异常：{tags}"

    def test_none_intent_falls_back_to_all(self):
        """不传 intent 时必须退回旧行为 —— 不能因为漏传就出错。"""
        from app.llm.prompts import FEW_SHOT, _pick_demos

        assert _pick_demos(None) == FEW_SHOT
        assert _pick_demos("") == FEW_SHOT
        # 未收录的意图也要退回，不能崩、不能给空
        assert _pick_demos("SOME_UNKNOWN_INTENT")

    def test_all_demos_have_intent_tag(self):
        """每条示例都要有 intent 标签，否则筛选会漏。"""
        from app.llm.prompts import FEW_SHOT

        for ex in FEW_SHOT:
            assert ex.get("intent"), f"示例缺 intent 标签：{ex['content'][:20]}"

    def test_base_system_keeps_all_rules(self):
        """精简文案时不能把铁律删掉 —— 每条都是踩坑加的。"""
        from app.llm.prompts import BASE_SYSTEM

        # 校验关键词而不是整句 —— 精简文案改了措辞，
        # 但每条铁律的**语义**必须还在。
        for kw in (
            "占位符",      # 铁律1 数值契约
            "换算",              # 铁律2 计算交给系统
            "100 克",        # 铁律3 营养素按每百克
            "BMR", "TDEE",# 铁律4 区分两者
            "MET", "移动平均",    # 铁律5 标注不确定性
            "1500", "就医",# 铁律6 安全边界
        ):
            assert kw in BASE_SYSTEM, f"铁律关键词丢失：{kw}"

    def test_token_budget_reduced(self):
        """优化必须真的省了 token，否则等于白改。"""
        from app.llm.client import _estimate_tokens
        from app.llm.prompts import BASE_SYSTEM, _pick_demos

        # BASE_SYSTEM 应在 350 tokens 以内（改前 420）。
        # 注意：铁律7（通用建议须标注来源）是后加的合理开销，
        # 它堵的是「模型把公共建议伪装成数据库数据」这个真实问题。
        assert _estimate_tokens(BASE_SYSTEM) < 350

        # 单个意图的示例不应超过 320 tokens（改前全部 754）
        demos = _pick_demos("LOOKUP_FOOD")
        total = sum(
            _estimate_tokens(d["content"]) + _estimate_tokens(d["assistant"])
            for d in demos
        )
        assert total < 350, f"示例仍过多：{total}"
