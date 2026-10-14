"""通用算术原料。

## 为什么需要

「步行消耗多少」「慢跑消耗大吗」这类问题，系统原有的每个意图都答不了
—— 它们要么只返回 BMR/TDEE，要么需要精确时长。

但这类问题的本质是**算术**：MET × 体重 × 小时。
只要把 MET 和体重作为槽位给出去，模型就能用
`{{calc: met_1 * weight_kg_1 * 0.5}}` 自己算，结果可追溯。

这就是「可信算术」能根治漏数字的原因：
不再需要为每个问法穷举意图，只要原料齐了，模型就能算。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Alias, ExerciseItem

# 常见运动的别名 —— 库里有的才返回，查不到的不硬塞
_POPULAR = (
    "慢走", "散步", "快走", "慢跑", "跑步", "快跑",
    "骑车", "游泳", "爬山", "跳绳", "瑜伽", "篮球", "羽毛球", "力量训练",
)


@dataclass
class ExerciseRef:
    """一项运动的算术原料。"""

    alias: str
    met: float


def list_common_exercises(db: Session, limit: int = 12) -> list[ExerciseRef]:
    """取常见运动的 MET，供模型做算术。

    只用别名表里的叫法 —— 数据集原始名是
    「步行：步行：4.5-5.1公里/小时，中等速度」这种三级学术命名，
    模型引用不了，用户也看不懂。
    """
    rows = db.execute(
        select(Alias.alias, ExerciseItem.met)
        .join(ExerciseItem, Alias.target_id == ExerciseItem.id)
        .where(
            Alias.target_type == "exercise",
            Alias.alias.in_(_POPULAR),
            ExerciseItem.met.isnot(None),
            ExerciseItem.met > 0,
        )
        .order_by(ExerciseItem.met.desc())
    ).all()

    seen: set[str] = set()
    out: list[ExerciseRef] = []
    for alias, met in rows:
        if alias in seen or not met:
            continue
        seen.add(alias)
        out.append(ExerciseRef(alias=alias, met=float(met)))
        if len(out) >= limit:
            break
    return out


def current_weight(db: Session) -> float:
    """算消耗用的体重：最近记录优先，退回档案。

    与 BMR 同一口径，避免同一页出现两个体重。
    """
    from app.services.body_service import get_profile
    from app.services.log_service import resolve_weight_for_bmr

    p = get_profile(db)
    weight, _, _ = resolve_weight_for_bmr(db, p)
    return float(weight)
