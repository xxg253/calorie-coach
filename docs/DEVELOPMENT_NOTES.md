# Calorie Coach · 热量摄入与运动消耗记录系统

> **P0 数据地基 + P1 后端 API 已完成**，P2 前端 / P3 智能对话待做。
>
> 设计文档在 `docs/`，日常使用看本文即可。

## 零、先看这个：5 条命令跑通全部功能

```bash
cd "E:/PythonProject/Calorie control agent"
PY=.venv/Scripts/python.exe

# 1. 数据自检（13 项应全绿）
$PY -m app.cli verify-import

# 2. 命令行演示（7 个核心场景）
$PY -m app.cli demo

# 3. 提问（支持追问）
$PY -m app.cli ask "西红柿炒鸡蛋多少热量"

# 4. 单元测试（210 个）
$PY -m pytest -q

# 5. 启动 API 服务 → 打开 http://127.0.0.1:8000/docs
$PY -m uvicorn app.main:app --reload --port 8000
```

API 另有独立验证脚本（需服务已启动）：
```bash
$PY scripts/check_api.py      # 71 项端到端检查
```

---

## 一、环境准备（已完成，你不需要重做）

项目已装好依赖、导入好数据。你直接用命令即可。

```bash
# 位置
cd "E:/PythonProject/Calorie control agent"

# Python 解释器（已创建的虚拟环境）
.venv/Scripts/python.exe
```

## 二、5 条命令验证全部功能

### 1. 初始化数据（只需跑一次，已跑过）
```bash
.venv/Scripts/python.exe -m app.etl.load_all
```
输出：
```
[1/5] 数据集 ETL… 食物 1215 / 运动 808 (合并 392，歧义 145 组)
[2/5] 中式食材补充… 83 条
[3/5] 烹饪倍率… 12 条
[4/5] 菜谱与配料… 117 道 / 548 条配料
[5/5] 别名… 107 条
```

### 2. 数据自检
```bash
.venv/Scripts/python.exe -m app.cli verify-import
```
**成功标准**：13 项全部 ✅，最后显示「✅ 全部通过」

### 3. 一键演示（最快看到效果）
```bash
.venv/Scripts/python.exe -m app.cli demo
```
跑 7 个核心场景，每条都能看到完整计算过程。

### 4. 逐条提问
```bash
.venv/Scripts/python.exe -m app.cli ask "200克米饭的热量"
.venv/Scripts/python.exe -m app.cli ask "西红柿炒鸡蛋多少热量"        # 会追问油量
.venv/Scripts/python.exe -m app.cli ask "西红柿炒鸡蛋炒的比较清淡"     # 指定清淡直接算
.venv/Scripts/python.exe -m app.cli ask "推荐几个低热量高蛋白的食物"
.venv/Scripts/python.exe -m app.cli ask "我175cm 78kg BMI是多少"
.venv/Scripts/python.exe -m app.cli ask "快走30分钟消耗多少"
.venv/Scripts/python.exe -m app.cli ask "琼脂多少热量"                # 会弹候选
```

交互模式（可以连续问）：
```bash
.venv/Scripts/python.exe -m app.cli ask
```

### 5. 记录与查看
```bash
# 记录
.venv/Scripts/python.exe -m app.cli record food "米饭（均值）" 200
.venv/Scripts/python.exe -m app.cli record ex "快走" 30
.venv/Scripts/python.exe -m app.cli record weight 78.5
.venv/Scripts/python.exe -m app.cli record dish "西红柿炒鸡蛋" 250 --method 炒 --oil 清淡

# 查看
.venv/Scripts/python.exe -m app.cli summary      # 今日汇总
.venv/Scripts/python.exe -m app.cli weight       # 体重趋势（含移动平均）
.venv/Scripts/python.exe -m app.cli methods      # 烹饪倍率表
.venv/Scripts/python.exe -m app.cli etl-check    # ETL 与去重自检
```

## 三、跑测试
```bash
.venv/Scripts/python.exe -m pytest -q
```
**当前状态**：201 个测试全部通过。

## 四、数据现状

| 项目 | 数量 | 说明 |
|---|---|---|
| 食物总数 | 1298 | 1166 条 USDA + 83 条中式手工 + 49 条去重合并 |
| 歧义食物 | 542 条 / 145 组 | 命中时会弹候选，如琼脂 26 vs 311 |
| 运动总数 | 808 | 其中 297 条日常活动（步行/家务/静坐） |
| 菜谱 | 117 道 | 548 条配料，零未匹配 |
| 烹饪方式 | 12 种 | 倍率 1.00–2.20 |
| 别名 | 107 条 | 口语化表达 → 主数据 |

## 五、数据从哪来

```
shujuju/combined_train.jsonl  +  combined_test.jsonl
        ↓ 解析（2487 条）
   食物 1643 + 运动 844
        ↓ 清洗脏值（36 条无热量丢弃）
        ↓ 去重（ratio ≤ 0.15 取均值，> 0.15 保留歧义）
   食物 1215 / 运动 808
        ↓ 补充中式食材（83 条）
   食物 1298
        ↓ 合并菜谱（117 道）+ 烹饪倍率（12 种）+ 别名（107 条）
   ✅ 可用
```

**中式食材为什么必须手工补**：数据集源自 USDA 标准库，以美式食材为主。
实测确认缺失**猪里脊、鸡胸、鸡腿、青椒**这些中国用户最常吃的东西。
83 条补充数据依据《中国食物成分表》标准值。

**基础物料为什么手工补**：实测确认数据集缺**植物油（899 kcal）**、食盐、淀粉、料酒。
没有油数据，所有炒菜热量会系统性偏低一半 —— 因为油占炒菜总热量 30%–55%。

## 六、烹饪倍率表（实测查证数据）

| 做法 | 倍率 | 用油 | 依据 |
|---|---|---|---|
| 蒸 / 煮 / 焯水 | ×1.00 | 0g | 不需额外用油 |
| 凉拌 | ×1.10 | 3g | 少量香油 |
| 炖 / 焖 / 卤 | ×1.18 | 5g | 少量油 |
| 烤 | ×1.25 | 6g | — |
| 炒（少油） | ×1.25 | 5g | 急火快炒 |
| 炒（适中） | ×1.45 | 10g | 一平勺≈10g |
| 炒（多油） | ×1.70 | 15g | 炒一个50g鸡蛋用近15g油 |
| 煎 | ×1.60 | 12g | — |
| 炸 | ×2.20 | 25g | 吸油率10-20%，炸散鸡蛋达43% |

## 七、目前还没做的（P1 起）

| 功能 | 状态 |
|---|---|
| FastAPI 接口 | ❌ 未做（P1） |
| React 前端 | ❌ 未做（P2） |
| 大模型对话 | ❌ 未做（P3，需要你的 API Key） |
| 向量检索 | ❌ 未做（P3） |
| LangChain / LangGraph 编排 | ❌ 未做（P3） |

**现在能用命令行验证的**：食物查询、菜谱计算、烹饪倍率、运动消耗、BMI、推荐、记录、汇总、体重趋势。

## 八、接大模型（P3 时需要）

编辑 `.env`：
```
DASHSCOPE_API_KEY=sk-你的真实Key
```
现在这行是空的，所以 LLM 相关功能还没接。核心记账功能不依赖 LLM，红线七。

## 九、数据库说明

当前用 **SQLite**（`data/calorie_coach.db`），因为本机没装 Docker 和 PostgreSQL。

**换 PostgreSQL**：改 `.env` 一行即可
```
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/calorie_coach
```
然后
```bash
docker compose up -d
.venv/Scripts/python.exe -m app.etl.load_all
```
代码不用改 —— `FlexibleVector` 类型让同一份模型在两种后端都能跑。

## 十、常见问题

**Q：改了 CSV 种子数据怎么生效？**
```bash
.venv/Scripts/python.exe -m app.etl.load_all   # 幂等，可重复跑
```

**Q：想清空重来？**
```bash
.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'.'); from app.db import drop_all, init_db; drop_all(); init_db()"
.venv/Scripts/python.exe -m app.etl.load_all
```

**Q：pip 安装报找不到 pgvector>=0.7？**
用阿里镜像，pip 会自动挑兼容版本（0.5.1 功能一致）：
```bash
.venv/Scripts/python.exe -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
```
---

# P1 · API 接口说明

启动服务后访问 **http://127.0.0.1:8000/docs** 即可交互式测试所有接口（FastAPI 自动生成的 Swagger 文档）。

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

## 接口分组（31 个）

### 查询类
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/foods/search?keyword=米饭` | 搜索食物（搜索框用） |
| GET | `/api/foods/lookup/{name}` | 按名称查（别名/精确/基础名/模糊四级降级） |
| GET | `/api/foods/{id}` | 食物详情（53 营养素） |
| POST | `/api/foods/kcal` | 算热量 `{name:"米饭", grams:200}` |
| GET | `/api/recipes?category=家常菜` | 菜谱列表 |
| GET | `/api/recipes/search?keyword=炒` | 搜索菜谱 |
| GET | `/api/cooking-methods` | 烹饪倍率表 + **数据出处** |
| POST | `/api/recipes/calc` | 算菜谱热量（缺油量返回追问选项） |
| GET | `/api/exercises/search?keyword=快走` | 搜索运动 |
| GET | `/api/exercises/daily` | 日常活动（**步行优先**） |
| GET | `/api/exercises/daily/grouped` | 日常活动按类目分组 |
| GET | `/api/exercises/categories` | 运动类目 |
| POST | `/api/exercises/calc` | 算消耗 `{name:"快走", duration_min:30}` |
| GET | `/api/recommend?max_kcal=80&min_protein=5` | 低热量高蛋白推荐 |

### 记录类
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/intake/preview` | **预览不落库**，返回确认话术 |
| POST | `/api/intake` | 确认后创建记录 |
| GET | `/api/intake?date=2026-10-11` | 查某天饮食 |
| PATCH | `/api/intake/{id}` | 校正克重（自动重算热量） |
| DELETE | `/api/intake/{id}` | 撤销 |
| POST | `/api/exercise-logs` | 创建运动记录 |
| GET | `/api/exercise-logs?date=` | 查某天运动 |
| DELETE | `/api/exercise-logs/{id}` | 撤销 |
| POST | `/api/weight` | 记录体重（一天一次，同日覆盖） |
| GET | `/api/weight?days=30` | 体重记录 |

### 看板类
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/summary?date=` | 日汇总（BMR 与 TDEE **同时返回**） |
| GET | `/api/summary/range?days=7` | 区间汇总（画图表用） |
| GET | `/api/energy-target` | 能量目标 + 安全警告 |
| GET | `/api/weight/trend?days=30` | 体重趋势（**含 7 日移动平均**） |
| GET | `/api/bmi?weight_kg=&height_cm=` | BMI（中国 + WHO 双标准） |
| GET/PUT | `/api/profile` | 档案 |

### 系统
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 + 数据量 |
| GET | `/api/meta` | 倍率依据、数据来源、7 条设计红线 |

## 关键接口示例

**查食物命中歧义（红线四）**
```bash
curl "http://127.0.0.1:8000/api/foods/lookup/琼脂"
```
```json
{
  "found": false,
  "need_disambiguation": true,
  "candidates": [
    {"name": "琼脂", "kcal_per_100g": 26.0},
    {"name": "琼脂", "kcal_per_100g": 311.0}
  ],
  "message": "「琼脂」有 3 条记录且热量差异较大，请选择具体是哪一种"
}
```

**菜谱追问流程（用户设计的核心交互）**
```bash
# 第一次：没说油量 → 返回追问
curl -X POST "http://127.0.0.1:8000/api/recipes/calc" \
  -H "Content-Type: application/json" \
  -d '{"name":"西红柿炒鸡蛋","grams":250}'
```
```json
{
  "need_clarify": true,
  "clarify_message": "「西红柿炒鸡蛋」默认做法是「炒」，请问用油量是清淡、适中还是油腻？",
  "oil_level_options": [
    {"level": "清淡", "multiplier": 1.25, "oil_grams": 5.0},
    {"level": "适中", "multiplier": 1.45, "oil_grams": 10.0},
    {"level": "油腻", "multiplier": 1.70, "oil_grams": 15.0}
  ]
}
```
```bash
# 第二次：指定油量 → 出结果
curl -X POST "http://127.0.0.1:8000/api/recipes/calc" \
  -H "Content-Type: application/json" \
  -d '{"name":"西红柿炒鸡蛋","grams":250,"oil_level":"清淡"}'
```

**两阶段记录（红线三：写库需确认）**
```bash
# 第一步：预览（不写库）
curl -X POST "http://127.0.0.1:8000/api/intake/preview" \
  -H "Content-Type: application/json" -d '{"name":"米饭（均值）","grams":200}'
# → {"kcal":232.0,"confirm_prompt":"即将记录：米饭（均值） 200g = 232.0 千卡，确认吗？"}

# 第二步：用户确认后才创建
curl -X POST "http://127.0.0.1:8000/api/intake" \
  -H "Content-Type: application/json" -d '{"name":"米饭（均值）","grams":200,"meal_type":"lunch"}'
```

**简写 `name` 字段**
`name` 会自动判断是食物还是菜谱 —— **先查菜谱再查食物**，因为「西红柿炒鸡蛋」当食物猜必然错。

## CORS
已配置 `localhost:5173`、`127.0.0.1:5173`、`localhost:3000`，P2 前端可直接对接。

---

## P1 期间修复的 bug（供参考）

| 问题 | 原因 |
|---|---|
| 运动记录接口 500 | `ExerciseLog` 没有 `grams` 字段但代码直接访问 |
| 体重趋势接口 500 | `WeightItem(**p)` 字段名不匹配（`bmi` vs `bmi_value`） |
| 日常活动列表看不到步行 | 按 MET 升序排，睡觉(0.95)占前排，步行排到 50 名后 |
| preview 404 | schema 字段名 `food_name` 与前端传的 `name` 不一致 |

第 3 条值得注意：**这不是性能问题而是产品问题**。用户最需要的「日常走路」排在第 50 名之后，功能等于不存在。

---

# P3 · 智能对话（RAG + LLM + LangGraph）

## 一、启动

```bash
PY=.venv/Scripts/python.exe

# 1. 灌入知识切片（约1137条，首次会调用嵌入API，之后走缓存）
$PY -m app.retrieval.index_chunks

# 2. 启动服务
$PY -m uvicorn app.main:app --reload --port 8000

# 3. 验证（需服务已启动）
$PY scripts/check_chat.py    # 33 项端到端检查
```

**首次灌切片**会调用 text-embedding-v4 约 1200 token，耗时 1-2 分钟。
之后有本地缓存（`data/embed_cache.json`），重跑秒级完成。

## 二、对话接口

```bash
# 单轮问答
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"200克米饭的热量是多少"}'

# 多轮追问（追问时带上 session_id）
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"西红柿炒鸡蛋多少热量"}'
# → {"needs_clarification":true,"clarify_message":"用油量偏清淡、适中还是油腻？","session_id":"s_xxx"}

curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"偏清淡","session_id":"s_xxx"}'
# → {"answer":"...约 429.5 千卡..."}

# 流式（SSE）
curl -N -X POST http://127.0.0.1:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message":"快走30分钟消耗多少"}'
# 事件顺序：intent → tool → token → done

# 不使用 LLM（红线七：核心功能不依赖 LLM）
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"200克米饭的热量","use_llm":false}'

# 查看对话链路设计（面试可直接引用）
curl http://127.0.0.1:8000/api/chat/design
```

## 三、数值可信化：占位符模板填充

**这是本项目最核心的技术决策。**

### 为什么不能用「白名单检测」

实测证据（真实调用 qwen-max）：
| 提问 | 模型答 | 实际 |
|---|---|---|
| 200克米饭热量 | 230千卡 | 数据库真值 **232** |
| 100克火龙果 | 60 | 知识库**没有** |
| 一杯珍珠奶茶 | 250 | 知识库**没有** |

模型会**自信编造任何数字**。我先做白名单检测，迭代 6 版全部失败：

| 版本 | 白名单大小 | 拦截率 |
|---|---|---|
| v2 | 1400 | 0/4 |
| v3 | 961 | 0/4 |
| v4 | 1217 | 0/4 |
| v6 | 177 | 2/5 |

**死结**：白名单必须允许合理衍生（取整、单位换算、烹饪倍率），
但每加一条衍生规则就多放行一批幻觉数字。不是调参能解决的。

### 改用「构造」而非「检测」

```
LLM 输出：「200克{{kcal_1}}约{{kcal_per_100g_1}}千卡/100克」
服务端填充：「200克米饭约232千卡，每100克116千卡」
```

**模型只输出占位符，根本看不到真值 —— 没有编造机会。**
正确性从「事后检测」变成「事前构造」，这是唯一能真正保证 100% 正确的做法。

双保险：模型若违规输出裸数字（实测会），会触发检测并在答案末尾提示。

## 四、已实测通过的对话场景

| 场景 | 结果 |
|---|---|
| 200克米饭的热量 | 232 千卡（数据库真值，非模型算） |
| 我175cm 78kg BMI | 25.7 超重，中国标准与 WHO 标准对比 |
| 快走30分钟消耗 | 137 千卡，标注 MET ±20% 误差 |
| 我的基础代谢 | BMR 1745.8 / TDEE 2400.5，主动区分两者 |
| 西红柿炒鸡蛋（追问） | 第1轮追问油量 → 第2轮 429.5 千卡 |
| 一杯珍珠奶茶 | **拒答**，说明知识库无此数据 |
| 琼脂多少热量 | 弹 3 个候选，含 26 与 311（红线四） |
| 减脂期怎么吃主食 | RAG 命中，5 条引用 |
| 为什么体重不掉 | 616 字回答，解释移动平均与平台期 |

## 五、意图识别准确率 100%（17/17）

规则优先 + LLM 兜底。规则在确定场景下准确率 100% 且零成本。

中文单位全部正确：
```
一斤米饭 = 500g    半斤 = 250g    两斤 = 1000g
两个鸡蛋 = 200g    三两肉 = 150g
175cm（身高）不会被误算成 78000 克
```

## 六、检索层

**1137 条切片**：food 732 / exercise 200 / recipe 117 / alias 78 / knowledge 10

混合检索（向量 HNSW + 关键词 LIKE + RRF 融合）实测：
```
❯ 白饭的热量       → 白饭别名切片（116 kcal）
❯ 鸡胸肉热量       → 鸡胸肉（熟）165 + 别名 133
❯ 西红柿炒鸡蛋怎么做 → 菜谱排第一
❯ 快走能消耗多少     → MET 3.5 切片
```

**RRF 融合做了关键词加权 ×2.5** —— 食物名全是专有名词，
向量检索对专名区分度差（问「白饭」会召回「炒饭」），这是领域特点不是 hack。

## 七、P3 期间抓到的 bug（都是实测踩出来的）

1. **ORM 同名属性遮蔽**：`select(Alias, FoodItem)` 的行对象 `.alias` 返回模型实例而非字符串，拼进文本成 `"<Alias 白饭>"`，SQLAlchemy 当比较表达式解析报错。必须 `select(Alias.alias, FoodItem)`。
2. **few-shot 当历史续写**：把 few-shot 作为独立消息时，模型当成真实对话历史，用户问 BMI 却把示例里的牛肉面/奶茶挨个答一遍。必须折叠进同一条 user 消息。
3. **意图提前 return 丢槽位**：`LOOKUP_FOOD` 分支 return 前没抽 `food_name`，导致工具返回 0 条结果。
4. **「175cm」被当克重**：BMI 问题算出 78000 克。加负向前瞻 + 按意图排除。
5. **「一斤」解析不出**：中文数量在单位前，`\d+` 匹配不到。加中文数字表。
6. **追问轮状态丢失**：`ChatResult` 不带 state，第2轮菜名丢失。
7. **追问轮 needs_llm 继承**：上一轮是追问轮设了 False，第2轮拿到答案后不调 LLM，返回空字符串。
8. **追问轮的菜名不在输入里**：用户只说「偏清淡」，模型看不到菜名。要把原问题一起传给它。
9. **占位符别名不匹配**：模型写 `protein_1`，工具返回键名是 `protein_per_100g`。加短名别名。
10. **别名全局状态污染**：用全局字典存别名会跨调用污染，改为随槽位传递。

---

# P2 · React 前端（完整体验）

## 启动（两个服务）

```bash
# 终端 1：后端
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000

# 终端 2：前端
cd frontend && npm install && npm run dev
```

打开 **http://127.0.0.1:5173**。三个页面：看板 / 记录 / 智能问答。

## 界面上的三个刻意设计

1. **BMR / TDEE / 额外运动三个数字同屏** —— 用户最大的误区是把基础代谢当日消耗。
2. **体重折线必须叠加 7 日移动平均** —— 日间 ±1.5kg 波动属正常，
   只画原始值用户看到噪音就放弃。
3. **推荐区可展开查看实际 SQL** —— 证明「低热量高蛋白」是 `kcal<=80 AND protein>=5`
   查出来的，不是向量猜的。

## 三层验收

```bash
cd frontend
npm run typecheck              # 类型
npm run build                  # 构建
node scripts/verify_ui.mjs     # 真浏览器 21 项 + 截图
```

**第三层不能省** —— P2 靠它抓到 5 个只有运行期才暴露的问题，
包括一个「确认弹窗永远不出现」的死循环 bug（前端漏传烹饪方式，后端反复返回追问）。

## 已完成的验证

| 项 | 结果 |
|---|---|
| pytest | **256 passed** |
| API 端到端 | **71/71** |
| 对话端到端 | **33/33** |
| 浏览器验收 | **21/21** |
| 生产构建 | 通过（echarts/antd 单独切块） |

详细说明见 `docs/前端说明P2.md`。

---

# P4 · 个人中心 + BMR 严格化

## 一处真实缺陷（用户追问「没有身高你BMI怎么算的」引出）

`compute_energy_targets` 原本用 `profile.weight_kg` —— 一个**冗余字段**。
只有部分写路径才会同步它，一旦漂移，BMR / TDEE / 目标热量 / 对话 / 推荐全部跟着错，
且没有任何提示。

**改法**（`log_service.resolve_weight_for_bmr`）：
- 优先取 **7 天内最近一次体重记录**
- 没有才退回档案值，并标记 `weight_source="profile"`
- 返回完整 `BMRBasis`，界面直接展示算式：

```
BMR = 10×78.2 + 6.25×175.0 − 5×28 + 5 = 78.2kg（2026-10-11 记录）
```

为什么取最近记录而不是最新一条：今天空腹称 78.0、昨晚餐后 79.2 是常态，
单点值会放大噪音。

## 个人中心 `/profile`

左表单 + 右「基础代谢怎么算的」。右侧是重点 —— **让数字可验证**：
公式、体重来源标签（绿=取自记录 / 橙=档案）、可读算式、TDEE、
目标热量的三条安全约束、公式误差提示。

未来接登录时 `profile` 表加 `user_id` 变多行，页面只需换取数方式，
**表单与 BMR 计算逻辑都不用改**。

## 换一批

用 **offset 分页而非随机**：顺序稳定、可完整遍历、不重复。
接口返回 `total` / `has_more`，实测 29 条按 8 条一批遍历累计零重复。

## 图表

7 天 → 14 天，能看出「周末 vs 工作日」的差异。

## 踩到的两个 bug

| Bug | 原因 | 怎么发现 |
|---|---|---|
| **数值字段全空** | `Form.Item` 直接子元素是 `Space.Compact`，而 antd Form 只向**直接子元素**注入 value/onChange。`InputNumber` 在里面一层收不到 | 浏览器验收 + **看截图**（断言全过但界面是空的） |
| Form disabled 阻断赋值 | 误判，根因还是上面那个 | 改成 Spin 后问题依旧才发现方向错了 |

第一个的教训：**迁移废弃 API 时不能只改到「不报废弃警告」**。
我为了消掉 `addonAfter` 的警告改用 `Space.Compact`，结果引入了更隐蔽的 bug。
已补「表单字段有初值」断言锁住这类问题。

## 验证

pytest 256 / API 71 / 浏览器 **32**（新增 11 项）/ 构建通过 —— 全绿。

---

# 一键启动

## 双击 `启动.bat` 即可

它会自动做这些事：

1. **端口检测** —— 8000/5173 被占用时提示并退出（避免 `10048` 端口冲突）
2. **环境自检** —— 检查 `.venv`、`.env`、Node.js 是否就位
3. **按需装依赖** —— `frontend/node_modules` 不存在时自动 `npm install`
4. **依次启动** —— 先起后端，等 5 秒再起前端（否则前端代理会先报错）
5. **自动打开浏览器** → http://127.0.0.1:5173

会弹出两个命令行窗口（标题分别是「热量助手-后端」「热量助手-前端」），
**关掉它们就是停止服务**。

## 双击 `停止服务.bat` 强制清理

按 PID 结束占用 8000 / 5173 的进程。启动脚本报端口占用时先跑这个。

## 手动启动（不用脚本时）

```
# 终端 1
.venv\Scripts\python.exe -m app.main

# 终端 2
cd frontend
npm run dev
```

---

# 修复：推荐运动被当成推荐食物

## 现象
问「给我推荐几个燃脂效率高的运动」，答案里满屏「（数据待核实）」，看起来像崩溃。

## 根因是四层叠加

### 1. 意图层没区分对象
`RECOMMEND` 一律进食物推荐分支，于是工具返回**紫菜、虾仁、鸡蛋白**。
模型想答跑步却拿不到任何运动数据，引用了不存在的槽位
（`kcal_running`、`kcal_jumping_rope`…），全部渲染成「数据待核实」。

**修法**：新增 `EXERCISE_RECOMMEND` 意图，先判断推荐对象是运动还是食物。

### 2. 运动推荐工具根本不存在
只补意图没有用，得有对应工具。新增
`exercise_service.recommend_high_burn_exercises()`，
按 MET 降序并用**用户真实体重**算 `kcal_per_hour`。

### 3. 排序策略反了
纯按 MET 降序，推荐出来的是：
```
直排轮滑，24.0公里/小时，最大努力    MET 14.0
功率自行车，201-270瓦，极高强度      MET 14.0
冰上舞蹈                             MET 14.0
```
MET 最高但普通人做不了，而且列表里连跑步、跳绳都没有。

**改法**：只从**别名表里的常用运动**中选（跳绳/快跑/跑步/慢跑/篮球/爬山/游泳/骑车…），
MET 卡在 6~12.5。结果变成了能直接用的列表。

### 4. build_slots 静默丢弃字符串字段（最隐蔽）
```python
if not isinstance(v, (int, float)):
    continue          # ← name / line 全被丢掉
```
工具返回的 `name`、`line` 是字符串，压根没进槽位。
所以模型在 Prompt 里**看不到 `{{line_N}}` 存在**，只能自己编名字 ——

结果 MET 12.3（跳绳的）被写到「跑步（8 km/h）」名下，数值张冠李戴。

**修法**：
- `build_slots` 接受字符串
- 工具返回 `line` 整句槽位，把名称与数字**绑定成一个值**
- 护栏把字符串槽位内的数字也计入合法（否则满屏误报）

## 顺带修的数据错误
「跳绳」别名原本指向 `有氧运动：跳绳`，但数据集里没有这个类目，
导入时 MET 猜测把它配到了**武术**（MET 10.3）。已改指
`体育运动：跳绳：快速，每分钟120-160次`（MET 12.3）。

## 测试断言的两个教训

用户改档案后（175cm/28岁 → 183cm/22岁，78kg → 93.5kg），
写死期望值的断言全部失败：

| 断言 | 写死的值 | 新档案下的正确值 |
|---|---|---|
| BMI | 25.7 | 27.9 |
| 快走 30 分钟 | 137 | 163.6 |

**那是测试写错，不是系统错。** 全部改成动态计算：
- BMI 由接口返回的 weight/height 反算
- 运动消耗由库里的 MET × 当前体重 × 时长算出
- 档案只校验「字段是否在合法区间」，不校验具体值
- 数值匹配改用容差（答案可能给 163.6 也可能给 164）

## 最终结果
```
跳绳（MET 12.3，每小时约 1150.0 千卡）
快跑（MET 9.8，每小时约 916.3 千卡）
跑步（MET 8.3，每小时约 776.1 千卡）
慢跑（MET 7.0，每小时约 654.5 千卡）
游泳（MET 7.0，每小时约 654.5 千卡）
```
护栏无警告，数字全部来自数据库且归属正确。

验证：pytest **264** / API **73** / 对话 **34** —— 全绿。

---

# 修复：切页后对话消失

## 现象
在智能问答页聊完一轮，切到看板再回来，对话空白。

## 根因
`msgs` 只存在组件的 `useState` 里。React 路由切换会卸载组件，
state 随之销毁 —— 前端没有任何持久化。

有意思的是：**消息其实早就落库了**（`conversations` 86 条 / `chat_messages` 200 条），
只是没有接口把它读出来。等于数据存了但取不到。

## 修法：双保险

### 后端补两个读接口
```
GET /api/chat/sessions                      # 会话列表（最近优先）
GET /api/chat/session/{session_id}/messages # 某个会话的完整消息
```
`citations` 从 JSON 字符串解析回数组，`intent` 一并返回（界面要显示意图标签）。

### 前端两条恢复路径
1. **localStorage**（首选）—— `msgs` 变化即落盘，
   `useState` 的初值直接从它读，所以切页回来组件刚挂载就有内容
2. **后端历史**（兜底）—— 本地为空时拉最近一次会话。
   换浏览器、清了缓存也能续上

另加「正在恢复上次的对话…」提示，避免拉历史的空窗期被误认为对话丢了。

**清空按钮只清界面与 session 引用，不删后端历史** —— 那是可回看的记录。

## 验收（scripts/verify_session.mjs，9 项）
```
✅ 第一轮回答成功
✅ 切到看板
✅ 切回后消息数量不变（4 → 4）
✅ 切回后回答内容仍在
✅ 刷新后消息仍在（4 条）          ← 比用户描述的场景更严
✅ localStorage 已保存
✅ 清缓存后从后端恢复（2 条）
✅ 无页面错误
```

## 顺带修的断言问题
BMI 断言写死了「超重」，但档案改成 183cm/78.2kg 后 BMI 是 23.4（**正常**）。
分类不该写死，已放宽为只校验数值。

同理 `check_api.py` 的「档案身高 == 175」也改成了「字段在合法区间」。

**教训同上一轮：涉及用户可改数据的断言，绝不能写死期望值。**

## 测试里的一个坑
`db_session` 是 session 级 fixture，多个测试共享一个库；
`conversations.thread_id` 有 UNIQUE 约束 —— 同一个 id 造两次直接 IntegrityError，
后续测试还会连环 PendingRollbackError。
每个测试必须用各自唯一的 thread_id。

## 验证
pytest **268** / API **73** / 对话 **34** / 浏览器 **32** / 会话专项 **9** —— 全绿。

---

# 修复：问「西红柿含什么元素」满屏数据待核实

## 现象
问「西红柿里面含什么元素」，答案里热量/蛋白质/脂肪/碳水全是「（数据待核实）」。

## 三层根因

### 1. 意图落错分支
`LOOKUP_FOOD` 的触发词只有 `热量/卡路里/kcal/营养`，不含「元素」「成分」「维生素」。
于是「西红柿里面含什么元素」落进 `KNOWLEDGE_QA` ——
而该意图**不查数据库**，模型只能凭空引用 `kcal_1`、`protein_per_100g_1`。

已补齐触发词：成分、元素、维生素、矿物质、含什么、有什么营养、含量、多少蛋白…

### 2. 食物名抽错（正则法不够用了）
补完触发词后又出新问题 —— 抽出来的是：
```
西红柿里面含什么元素 → food_name = '西红柿里面'
牛肉含多少蛋白质      → food_name = '牛肉含'
```
停用词剥离总会漏新词式，穷举下去没完没了。

**改为：拿库里真实存在的名字做最长匹配**（1251 个候选，进程内缓存一次）。
抽出来的一定是可查的，「白饭」这种别名也能精确命中。

> 教训：与其穷举停用词，不如让抽取结果**必须来自可信来源**。

### 3. 数据集根本没有维生素字段
即使查到了西红柿，库里只有：
```
热量、蛋白质、脂肪、碳水、膳食纤维、钠、钙、铁
```
**没有任何维生素与微量元素。** 但模型不知道这件事，会凭常识列十种维生素并编数值。

**修法**：工具返回里显式加两个字段
```python
_available     = "热量、蛋白质、脂肪、碳水化合物、膳食纤维、钠、钙、铁"
_not_available = "本数据集未收录维生素、矿物质微量元素、糖类明细，不要列举这些项"
```
同时在意图提示里强调「只能列举 `_available` 里的字段」。

## 现在的输出
```
❯ 西红柿里面含什么元素

西红柿主要含有以下营养素（每100克的含量）：
- 热量：20 千卡      - 蛋白质：0.9 克
- 脂肪：0.2 克       - 碳水化合物：4 克
- 膳食纤维：0.5 克   - 钠：5 毫克
- 钙：10 毫克        - 铁：0.4 毫克

知识库里没有收录维生素与矿物质微量元素的数据，所以无法提供这些信息。

❯ 西兰花含哪些维生素

知识库里没有收录维生素数据，所以我无法提供西兰花具体含有哪些维生素的信息。
不过，西兰花是一种营养丰富的蔬菜，通常富含多种维生素和矿物质。…
```
护栏**无警告** —— 该给的都给，不该编的一句没编。

## 过程中踩的 SQLAlchemy 2.0 坑
```python
db.execute(select(A, B).where(...))   # ❌ where 不能混进 select()
```
SQLAlchemy 2.0 要求 `.where()` 跟在 `.select()` 之后。写成链式直接 SyntaxError。

## 验证
pytest **272** / API **73** / 对话 **34** —— 全绿。
新增 4 个回归测试：意图分流、名称抽取、**抽出的名字必须真能查到**、数据边界必须声明。

---

# 体重记录可修改 / 可删除

## 问题
体重是「一天一次」的唯一键，但原来只有 `POST /api/weight`（写当天）。
用户某天称错了（穿了厚衣服、饭后称的）只能干瞪眼 —— 没有任何修改入口。

## 新增接口

```
PUT    /api/weight/{log_date}   修改指定日期的体重
DELETE /api/weight/{log_date}   删除指定日期的体重记录
```

三个设计决定：

**1. 只允许改已存在的日期。** 新增仍走 POST。
这样界面上「修改」和「记录」两个动作语义明确，不会误操作。
不存在时返回 404 而不是静默新建。

**2. BMI 与判定由服务端重算，不接受客户端传入。**
否则用户改个体重可能带上过期的 BMI，制造出「体重和 BMI 对不上」的数据。

**3. 删除时同步刷新档案里的冗余体重。**
虽然 BMR 已改为优先用最近记录，但档案值仍会显示给用户，不能留脏值。

## 前端

体重历史表新增「操作」列：

| 按钮 | 行为 |
|---|---|
| 修改 | 弹窗编辑，标题含日期，弹窗内显示原值与旧 BMI，并说明「日期不可改」 |
| 删除 | Popconfirm 二次确认，明确告知「BMI 与移动平均会重算」 |

**为什么用弹窗而不是行内编辑**：体重影响 BMR、移动平均、热量缺口一整条链。
行内编辑改错了用户没有「再确认一次」的机会。

改完后统一刷新 6 个 query key：`weight / bmi / weightTrend / profile / summary / target`。

## 验收（scripts/verify_weight_edit.mjs，11 项）

```
✅ 修改按钮与删除按钮存在
✅ 弹窗显示原值、标题含日期
✅ 体重已改 78.2kg → 77.5kg
✅ BMI 自动重算 23.35 → 23.14
✅ 已还原原值（验收脚本自己清理现场）
```

## 过程中踩的两个坑

### 1. 我用字符串替换误删了整段 UI
改代码时用 `s[:idx] + new + s[abs_end:]` 定位，结果 `idx` 之后到 `</Col>` 的
**全部 JSX 被删掉** —— 只剩逻辑，界面没了。类型检查能过（因为结构自洽），
是浏览器截图暴露的。

**教训：结构化文件（JSX）不要用字符串切片改写**，
要么用 Edit 工具做精确替换，要么整段重写。

### 2. antd v6 的两个选择器陷阱
```js
// 标签页用的是 Segmented，不是 Tabs —— .ant-tabs-tab 永远找不到
".ant-segmented-item:has-text('体重')"

// antd v6 会把两个中文字之间插空格：「保存」→「保 存」
// :has-text('保存') 匹配不上，要用类名
".ant-modal-footer .ant-btn-primary"
```

## 顺带修的验收脚本问题
「今日饮食记录列表非空」断言假设「今天必然有记录」，
用户清过数据或换一天跑就会莫名失败。改成**先自举一条今日记录再断言**。

自举时又踩了一次：猜错了 `/api/intake` 的请求体契约
（实际是 `{found, food:{name}}` 嵌套 + `food_name` 而非 `food`）。
**写接口调用前先 curl 一遍看真实结构**，别凭印象。

## 验证
pytest **276** / API **73** / 浏览器 **32** / 体重专项 **11** / 构建通过 —— 全绿。

---

# 根治：「数据待核实」的根因与解法

## 之前的误区

我前两轮一直在**调护栏**（加白名单、放宽阈值）。方向反了。

真正的规律是：

> 凡是问题本身需要数字、而系统没有对应工具的，模型就只能编，编了必被拦。

**「模型编数字」几乎总是系统能力缺失的信号，而不是模型的错。**

## 方案 B：可信算术（已实施）

不再禁止模型算，而是**允许它算，但只能用数据库给的数字算**。

```
{{calc: met_1 * weight_kg_1 * 30 / 60}}
        └─ 系统真实执行这个算式并填入结果
```

### 为什么这能根治

- 模型的**运算**是确定性的，不会产生幻觉
- 幻觉只可能来自**输入**，而输入全部来自数据库
- 于是「算」这一步不再有幻觉风险

关键突破：**不需要穷举意图**。只要 MET 与体重两个原料给出去，
「步行消耗多少」「慢跑大吗」「走路一小时能瘦多少」全都能算。

### 三层实现

1. **算术引擎** `app/llm/calculator.py`
   AST 白名单求值，**不是 eval**。只允许加减乘除与括号，
   变量必须来自槽位。23 项攻击测试全部拦截。

2. **渲染管线** `app/llm/guard.py`
   `{{calc:...}}` 在普通占位符之前求值，结果计入合法数字池
   —— 否则算对了也会被护栏自己判成幻觉。

3. **原料兜底** `app/services/exercise_ref.py`
   凡是涉及运动/消耗的路径，统一补 12 种常见运动 MET + 用户体重。

## 顺带补的能力缺口

| 缺口 | 新增 |
|---|---|
| 餐具份量换算（「一碗米饭」） | `app/core/portions.py` |
| 多食物对比（「米饭和面条哪个高」） | `COMPARE_FOOD` 意图 |
| 摄入目标（「减脂期该吃多少千卡」） | TDEE + 目标区间 + 蛋白建议 |
| 「千卡」不在热量触发词里 | 中文单位补齐 |

## 验证：泄漏率 9/21 → 0/21

`scripts/verify_no_hallucination.py` 覆盖 21 种问法，连续三轮稳定 0 泄漏：

```
✅ 步行能消耗多少热量    ✅ 走路消耗多少
✅ 快走能消耗多少        ✅ 运动能消耗多少
✅ 慢跑消耗大吗          ✅ 快走30分钟消耗多少
✅ 西红柿里面含什么元素  ✅ 请问吃完一个汉堡要跳多久的绳
✅ 我要减到正常范围需要减到多少公斤
✅ 100克米饭多少千卡     ✅ 一碗米饭等于多少运动
✅ 米饭和面条哪个热量高  ✅ 减脂期每天应该吃多少千卡
✅ 正常人一天睡几小时（不该拦的也没误拦）
```

## 安全边界（已验证）

```
❌ __import__('os').system('ls')   ❌ met_1.__class__
❌ print('hi')                     ❌ [1,2,3][0]
❌ met_1 ** 1000                   ❌ eval('1+1')
❌ 除以零                           ❌ 未知变量
```
30 项测试全绿。

## 当前状态说明

对话层验收暂时跑不通 —— **API 免费额度用完了**（HTTP 403）。
此时系统会走红线七的降级路径（LLM 不可用仍返回数据库结果），
这是设计内行为，不是 bug。充值后恢复。

离线验证全部通过：pytest **303** / 算术引擎 **30** / API **73**。
