# RetroSynthesis API Refactor

## 结构

```text
retro_refactor/
├── app.py                         # FastAPI 入口，保留 uvicorn app:app
├── scifinder_parse.py             # 兼容旧导入路径
└── retro_service/
    ├── api.py                     # FastAPI 路由层
    ├── schemas.py                 # Pydantic 请求模型
    ├── config.py                  # 环境变量与全局配置
    ├── http_client.py             # requests session/retry
    ├── chemistry.py               # RDKit/SMILES 工具
    ├── utils.py                   # LRU、数值、单位、排序工具
    ├── price.py                   # 自有价格/库存/CAS 查询模块
    ├── local_api.py               # 本地逆合成候选接口模块
    ├── planner.py                 # 本地逆合成路线生成模块
    ├── scifinder.py               # SciFinder 远程路线调用与格式化
    ├── formatter.py               # 统一 JSON 输出格式与路线合并
    ├── visualizer.py              # 路线图片生成
    └── orchestration.py           # 服务编排，同时执行本地路线和 SciFinder 路线
```

## 运行

```bash
pip install -r requirements.txt
export SCIFINDER_API_KEY="你的 SciFinder 接口 Key"
# 也兼容旧变量名：export RETRO_API_KEY="你的 Key"

# 先在 chemical_score 项目根目录启动评分服务（默认端口 9528）
python ../chemical_score/app.py

# 再启动本项目
uvicorn app:app --host 0.0.0.0 --port 7755 --reload
```

评分服务默认请求 `http://127.0.0.1:9528/v1/evaluations`，可通过以下环境变量覆盖：

- `CHEMICAL_SCORE_API_URL`：Chemical Score 单反应 POST 接口地址；
- `CHEMICAL_SCORE_API_TIMEOUT`：单次评分超时秒数，默认 30 秒。

## 本地候选过滤与排序

本地逆合成接口返回的候选在进入搜索树前依次执行：

1. 严格要求 `is_match == true`；
2. 规范化 `target_smiles` 并要求其与当前待拆分目标完全相同；
3. 规范化反应物并排除空反应物、自循环和重复候选；
4. 通过 HTTP POST 调用 `chemical_score/app.py` 的独立评分服务；
5. 按以下优先级展开：

```text
is_ai=false
    ↓
chemical_score 从高到低
    ↓
原模型/库存/收率综合分从高到低
    ↓
is_ai=true（非 AI 候选耗尽或均未解决后回退）
```

为了降低上游 Top-K 截断导致非 AI 候选没有被召回的概率，实际召回数量默认为规划
`top_k` 的 4 倍，完成过滤、化学评分和排序后再截断。可通过
`LOCAL_CANDIDATE_FETCH_MULTIPLIER` 调整。

候选和最终路线会额外返回：

- `chemical_score`：0–100 化学总分；
- `chemical_score_dimensions`：可行性、证据支持度、安全性和经济性分数；
- `chemical_score_coverage` 和 `chemical_score_coverage_details`；
- `chemical_score_flags`：恒等反应、元素无来源等关键标志；
- `model_score`：保留原候选模型分，避免与化学分混淆。

## 保留接口

- `POST /api/plan`
- `POST /api/plan/download`
- `GET /health`

`/api/plan` 仍返回：

```json
{
  "code": 200,
  "msg": "查询逆合成路线成功",
  "data": {
    "list": [],
    "page_index": 1,
    "page_rows": 0,
    "data_count": 0,
    "page_count": 1,
    "search_stats": {}
  }
}
```

## 关键变更

1. 本地规划和 SciFinder 远程路线在 `/api/plan` 中并发执行，等待两者都结束后返回，整体超时 600 秒。
2. SciFinder 路线插入 `data.list[0]`，其 route `id=0`，根结构 `structures[0].id=0`。
3. SciFinder 的价格、库存与价格统计使用 `PriceClient` 的自有接口；结构节点 CAS 优先原样保留 SciFinder 的 `cas_number`，仅在源数据缺失 CAS 时使用 `PriceClient` 查询结果兜底。
4. `scifinder_parse.py` 保留为兼容层，旧代码中的 `from scifinder_parse import retro_api_to_list` 不需要改。
