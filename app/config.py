"""应用配置：统一从 .env 读取，禁止硬编码密钥。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录（app/config.py -> app -> 根）
ROOT_DIR = Path(__file__).resolve().parent.parent

load_dotenv(ROOT_DIR / ".env")


def _resolve_db_url(url: str) -> str:
    """把 SQLite 相对路径转成基于项目根目录的绝对路径。

    为什么必须做：``sqlite:///./data/x.db`` 里的相对路径是相对**当前工作目录**解析的，
    而工作目录取决于你怎么启动 —— 在项目根跑是一个值，在 PyCharm 里右键 Run
    ``app/main.py`` 又是另一个（通常是 ``app/``），结果 SQLite 直接报
    ``unable to open database file``。

    与其规定「必须从哪个目录启动」，不如让路径不依赖工作目录。
    """
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return url  # PostgreSQL 等绝对 URL 不处理

    raw_path = url[len(prefix) :]
    path = Path(raw_path)
    if not path.is_absolute():
        path = ROOT_DIR / path
    # 父目录不存在时 SQLite 不会自动创建，先建好
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"{prefix}{path}"


class Settings:
    """全局配置单例。所有环境变量集中在此，代码其他位置不读 os.environ。"""

    def __init__(self) -> None:
        self.app_env: str = os.getenv("APP_ENV", "dev")
        self.log_level: str = os.getenv("LOG_LEVEL", "INFO")

        # 数据库
        self.database_url: str = _resolve_db_url(
            os.getenv(
                "DATABASE_URL",
                "postgresql+psycopg://postgres:postgres@localhost:5432/calorie_coach",
            )
        )
        self.vector_dim: int = int(os.getenv("VECTOR_DIM", "1024"))

        # 大模型
        self.dashscope_api_key: str = os.getenv("DASHSCOPE_API_KEY", "")
        self.openai_base_url: str = os.getenv(
            "OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        self.llm_model: str = os.getenv("LLM_MODEL", "qwen3-max")
        self.embed_model: str = os.getenv("EMBED_MODEL", "text-embedding-v4")

        # ── 思考模式开关（性能关键）──
        # qwen3 系列默认开启深度思考，会先花大量 token 推理再回答。
        # 实测同一提问：开启 14.86s / 关闭 5.35s，慢 2.8 倍。
        #
        # 本项目的任务是「查库 + 引用数字 + 组织语言」，
        # 不需要复杂推理，因此默认关闭。
        # 若换用推理型模型做开放式问答，可置 LLM_ENABLE_THINKING=true。
        self.llm_enable_thinking: bool = (
            os.getenv("LLM_ENABLE_THINKING", "false").lower() == "true"
        )

        # 单用户档案（演示模式，无登录）
        self.profile_gender: str = os.getenv("PROFILE_GENDER", "male")
        self.profile_age: int = int(os.getenv("PROFILE_AGE", "28"))
        self.profile_height_cm: float = float(os.getenv("PROFILE_HEIGHT_CM", "175"))
        self.profile_weight_kg: float = float(os.getenv("PROFILE_WEIGHT_KG", "78"))
        self.profile_activity_level: float = float(os.getenv("PROFILE_ACTIVITY_LEVEL", "1.375"))
        self.profile_goal: str = os.getenv("PROFILE_GOAL", "lose")

        # 安全边界
        self.min_intake_kcal_male: int = int(os.getenv("MIN_INTAKE_KCAL_MALE", "1500"))
        self.min_intake_kcal_female: int = int(os.getenv("MIN_INTAKE_KCAL_FEMALE", "1200"))
        self.min_target_bmr_ratio: float = float(os.getenv("MIN_TARGET_BMR_RATIO", "1.0"))

        # 路径
        self.data_raw_dir: Path = ROOT_DIR / "data" / "raw"
        self.data_seed_dir: Path = ROOT_DIR / "data" / "seed"

    @property
    def is_llm_configured(self) -> bool:
        """LLM 未配置密钥时，核心记账功能仍可用（红线七）。"""
        return bool(self.dashscope_api_key) and "sk-" in self.dashscope_api_key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()