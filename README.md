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
export SCIFINDER_API_KEY="你的 SciFinder 接口 Key"
# 也兼容旧变量名：export RETRO_API_KEY="你的 Key"

uvicorn app:app --host 0.0.0.0 --port 7755 --reload
```

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
3. SciFinder 的价格、库存、CAS 与价格统计只使用 `PriceClient` 的自有价格接口，不再使用 SciFinder 返回的 `overall_price` 或 `building_blocks`。
4. `scifinder_parse.py` 保留为兼容层，旧代码中的 `from scifinder_parse import retro_api_to_list` 不需要改。
