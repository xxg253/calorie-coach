"""ORM 模型统一导出。init_db() 依赖此处的导入来注册所有表。"""

from app.models.catalog import (
    Alias,
    CookingMethod,
    ExerciseItem,
    FoodItem,
    Recipe,
    RecipeComponent,
)
from app.models.knowledge import KnowledgeChunk, UserFoodCorrection
from app.models.user_data import (
    ChatMessage,
    Conversation,
    ExerciseLog,
    IntakeLog,
    Profile,
    WeightLog,
)

__all__ = [
    "Alias",
    "ChatMessage",
    "Conversation",
    "CookingMethod",
    "ExerciseItem",
    "ExerciseLog",
    "FoodItem",
    "IntakeLog",
    "KnowledgeChunk",
    "Profile",
    "Recipe",
    "RecipeComponent",
    "UserFoodCorrection",
    "WeightLog",
]