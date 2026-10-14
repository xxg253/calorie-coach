"""FastAPI 入口。

启动（任选其一，都不依赖当前工作目录）：
    PyCharm：直接右键 Run 'main'
    命令行：.venv/Scripts/python.exe -m app.main
    传统式：.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
文档：
    http://127.0.0.1:8000/docs
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

# 直接 `python app/main.py` 或在 PyCharm 里右键 Run 时，sys.path[0] 是 app/ 而非项目根，
# 下面的 `from app import ...` 会 ModuleNotFoundError。
# 这段必须放在所有 app.* 导入之前，因此提到模块顶部。
_ROOT = _Path(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from app import __version__
from app.api import routes_chat, routes_dashboard, routes_log, routes_query
from app.config import settings
from app.db import SessionLocal, db_backend_name, init_db
from app.schemas import HealthResponse

app = FastAPI(
    title="热量摄入与运动消耗记录系统",
    description=(
        "Calorie Coach —— 饮食热量与运动消耗记录、RAG 辅助系统\n\n"
        "**核心设计**\n"
        "- 结构化查询全部走 SQL（`WHERE kcal<=80 AND protein>=5` 向量库做不了）\n"
        "- 所有 kcal 加减乘除在 Python 侧完成，LLM 不参与计算\n"
        "- 同名歧义必须弹候选，绝不静默取第一条\n"
        "- 数值均带来源标注与不确定性说明"
    ),
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
)

# P2 前端开发时的跨域配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite 默认
        "http://127.0.0.1:5173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_query.router)
app.include_router(routes_log.router)
app.include_router(routes_dashboard.router)
app.include_router(routes_chat.router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception):
    """未捕获异常统一处理。

    开发期返回真实原因便于排查（生产环境应改为只返回 500 + 通用文案，
    并把细节写日志 —— 健康类应用不能把内部错误直接暴露给用户）。
    """
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=500,
        content={
            "detail": f"{type(exc).__name__}: {exc}",
            "path": str(request.url.path),
            "hint": "开发模式返回真实错误便于排查；生产部署时应改为脱敏",
        },
    )


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/api/health", response_model=HealthResponse, tags=["系统"], summary="健康检查")
def health():
    """健康检查。顺带返回数据量，前端可用它判断是否需要提示先导入数据。"""
    from app.models import ExerciseItem, FoodItem, Recipe

    counts = {"food": None, "exercise": None, "recipe": None}
    try:
        db = SessionLocal()
        try:
            counts["food"] = db.execute(
                select(func.count()).select_from(FoodItem)
            ).scalar()
            counts["exercise"] = db.execute(
                select(func.count()).select_from(ExerciseItem)
            ).scalar()
            counts["recipe"] = db.execute(
                select(func.count()).select_from(Recipe)
            ).scalar()
        finally:
            db.close()
    except Exception:  # noqa: BLE001  数据库不可用时不阻塞健康检查
        pass

    return HealthResponse(
        status="ok",
        backend=db_backend_name(),
        version=__version__,
        llm_configured=settings.is_llm_configured,
        food_count=counts["food"],
        exercise_count=counts["exercise"],
        recipe_count=counts["recipe"],
    )


@app.get("/api/meta", tags=["系统"], summary="项目元信息")
def meta():
    """返回项目设计与实现要点，供前端展示「数据从哪来」。"""
    return {
        "cooking_multiplier_basis": [
            "《中国居民膳食指南(2022)》：成年人每日烹调油 25-30g，一平勺约 10g",
            "河南省疾控中心：蒸煮炖焖几乎无需油；急火快炒 5-10g；煎制 10-15g；油炸吸油率 10%-20%",
            "科普中国：炸散鸡蛋吸油率 43%（炸茄盒 17%）",
            "Saguy & Dana (2003) Journal of Food Science：炒 3-7%、深炸 8-20%",
            "1g 油 = 9 kcal；油占炒菜总热量 30%-55%",
        ],
        "data_sources": {
            "usda": "ModelScope health_dataset（食物每100g营养 53 字段 + 运动 MET），2487 条",
            "manual_foods": "手工补充中式食材 83 条（数据集源自USDA 美式库，缺鸡胸肉/猪里脊/青椒）",
            "manual_oil": "手工补充基础物料 30 条（含植物油 899 kcal，数据集完全缺失）",
        },
        "design_rules": [
            "LLM 不做任何数值计算",
            "LLM 不产出未验证的数字",
            "写库必须用户确认",
            "同名歧义必须弹候选",
            "所有数值标注来源与不确定性",
            "热量目标由服务端校验",
            "核心记账功能不依赖 LLM",
        ],
        "formula_notes": {
            "bmi": "BMI = 体重kg / 身高m²，中国标准 ≥24 超重，WHO 标准 ≥25 超重",
            "bmr": "Mifflin-St Jeor (1990)：男 10×体重+6.25×身高−5×年龄+5",
            "tdee": "TDEE = BMR × 活动系数（1.2~1.9）",
            "exercise": "消耗 = MET × 体重kg × 时长小时",
            "dedupe": "同名食物热量差异 ≤15% 取均值，>15% 保留全部并弹候选",
        },
    }


@app.get("/", tags=["系统"], summary="服务信息")
def root():
    return {
        "service": "Calorie Coach API",
        "version": __version__,
        "docs": "/docs",
        "health": "/api/health",
        "endpoints": {
            "查询": [
                "GET  /api/foods/search?keyword=米饭",
                "GET  /api/foods/lookup/{name}",
                "GET  /api/foods/{id}",
                "POST /api/foods/kcal",
                "GET  /api/recipes /api/recipes/search",
                "POST /api/recipes/calc",
                "GET  /api/cooking-methods",
                "GET  /api/exercises/search /api/exercises/daily /api/exercises/categories",
                "POST /api/exercises/calc",
                "GET  /api/recommend",
            ],
            "记录": [
                "POST   /api/intake（支持 preview 先确认）",
                "GET    /api/intake",
                "PATCH  /api/intake/{id}（校正克重）",
                "DELETE /api/intake/{id}",
                "POST   /api/exercise-logs",
                "GET    /api/exercise-logs",
                "DELETE /api/exercise-logs/{id}",
                "POST   /api/weight",
                "GET    /api/weight",
            ],
            "看板": [
                "GET  /api/summary",
                "GET  /api/summary/range?days=7",
                "GET  /api/energy-target",
                "GET  /api/weight/trend?days=30",
                "GET  /api/bmi",
                "GET  /api/profile / PUT /api/profile",
            ],
        },
    }

# ==========================================================================
# 本地启动入口
# PyCharm 里右键 Run 'main' 即可启动；命令行等价于：
#   python -m app.main
# 正式部署不要用这个（生产要用 uvicorn/gunicorn 多进程 + --reload 关闭）。
# ==========================================================================
if __name__ == "__main__":
    import uvicorn

    # 传 app 对象而非字符串：字符串形式要求 uvicorn 能 import 到 app.main，
    # 而此刻 __main__ 与 app.main 是两个不同模块，字符串会导致重复建表/重复挂载。
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        reload=False,  # PyCharm 里改代码需手动重启；命令行可用 --reload
    )
