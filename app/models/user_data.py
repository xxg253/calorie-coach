"""用户数据模型：档案、饮食记录、运动记录、体重、对话。"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Profile(Base):
    """单用户档案。演示模式不做登录，全库只有一行。

    身高/年龄/性别是 BMR 的计算输入。
    """

    __tablename__ = "profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    gender: Mapped[str] = mapped_column(String(10), default="male")  # male/female
    age: Mapped[int] = mapped_column(Integer, default=28)
    height_cm: Mapped[float] = mapped_column(Float, default=175.0)
    weight_kg: Mapped[float] = mapped_column(Float, default=78.0)  # 冗余最近体重
    activity_level: Mapped[float] = mapped_column(Float, default=1.375)
    goal: Mapped[str] = mapped_column(String(10), default="lose")  # lose/maintain/gain
    target_weight_kg: Mapped[float | None] = mapped_column(Float)

    # 每日热量目标：服务端计算，绝不由 LLM 给出
    daily_calorie_target: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class IntakeLog(Base):
    """饮食记录流水。

    kcal_snapshot 是入库时快照：防止主数据后续更新导致历史记录漂移。
    """

    __tablename__ = "intake_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    logged_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False, index=True)
    log_date: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)

    food_id: Mapped[int | None] = mapped_column(ForeignKey("food_items.id"), index=True)
    recipe_id: Mapped[int | None] = mapped_column(ForeignKey("recipes.id"), index=True)
    item_name: Mapped[str] = mapped_column(String(150), nullable=False)  # 冗余，便于直接展示

    grams: Mapped[float] = mapped_column(Float, nullable=False)
    kcal_snapshot: Mapped[float] = mapped_column(Float, nullable=False)  # 这一条的最终热量

    meal_type: Mapped[str | None] = mapped_column(String(16))  # breakfast/lunch/dinner/snack
    cooking_method: Mapped[str | None] = mapped_column(String(32))  # 若为菜谱，记录所选烹饪方式
    oil_level: Mapped[str | None] = mapped_column(String(16))  # 清淡/适中/油腻

    # 计算过程留痕，便于用户核对与争议追溯
    calc_detail: Mapped[str | None] = mapped_column(Text)

    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual/chat/cli
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=True)  # 红线三：写库需确认

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (Index("ix_intake_date", "log_date", "meal_type"),)

    def __repr__(self) -> str:
        return f"<IntakeLog {self.item_name} {self.grams}g {self.kcal_snapshot}kcal>"


class ExerciseLog(Base):
    """运动消耗流水。

    met_snapshot 是入库时快照 —— MET 值会修订，快照保证历史可复现。
    """

    __tablename__ = "exercise_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    logged_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False, index=True)
    log_date: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)

    exercise_id: Mapped[int | None] = mapped_column(ForeignKey("exercise_items.id"), index=True)
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)

    duration_min: Mapped[float | None] = mapped_column(Float)
    met_snapshot: Mapped[float | None] = mapped_column(Float)
    kcal_snapshot: Mapped[float] = mapped_column(Float, nullable=False)

    # 步数/距离等其他计量方式
    quantity_value: Mapped[float | None] = mapped_column(Float)
    quantity_unit: Mapped[str | None] = mapped_column(String(16))  # step/km/min

    is_daily_activity: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(16), default="manual")
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (Index("ix_exlog_date", "log_date"),)

    def __repr__(self) -> str:
        return f"<ExerciseLog {self.item_name} {self.kcal_snapshot}kcal>"


class WeightLog(Base):
    """体重记录。用户决策：一天只记一次（数据库层唯一约束）。"""

    __tablename__ = "weight_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    log_date: Mapped[dt.date] = mapped_column(Date, nullable=False, unique=True)
    weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    body_fat_pct: Mapped[float | None] = mapped_column(Float)

    # BMI 落库而非实时算：分类标准会变，存历史必须锁定当时口径，
    # 否则用户看到的历史曲线会「变色」
    bmi_value: Mapped[float | None] = mapped_column(Float)
    bmi_category: Mapped[str | None] = mapped_column(String(16))
    bmi_standard: Mapped[str | None] = mapped_column(String(16))  # who/china

    source: Mapped[str] = mapped_column(String(16), default="manual")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        CheckConstraint("weight_kg > 0", name="ck_weight_positive"),
        CheckConstraint("weight_kg < 500", name="ck_weight_range"),
    )

    def __repr__(self) -> str:
        return f"<WeightLog {self.log_date} {self.weight_kg}kg BMI={self.bmi_value}>"


class Conversation(Base):
    """会话。LangGraph checkpoint 也存这里（P3）。"""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str | None] = mapped_column(String(200))
    thread_id: Mapped[str | None] = mapped_column(String(64), unique=True)  # LangGraph checkpoint key
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ChatMessage(Base):
    """对话消息。citations 必存 —— 可追溯与可评测。"""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id"), index=True)

    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user/assistant/system/tool
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # 引用的切片/主数据 id，用于溯源 UI
    citations: Mapped[str | None] = mapped_column(Text)  # JSON 数组字符串
    tool_calls: Mapped[str | None] = mapped_column(Text)  # JSON
    intent: Mapped[str | None] = mapped_column(String(32), index=True)

    latency_ms: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now(), index=True)