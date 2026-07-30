import logging
from concurrent.futures import ALL_COMPLETED, ThreadPoolExecutor, wait
from typing import Dict, Optional

from fastapi import HTTPException

from .config import (
    DEFAULT_API_TIMEOUT,
    DEFAULT_PRICE_TIMEOUT,
    DEFAULT_SCIFINDER_TIMEOUT,
    LOCAL_RETRO_API_URL,
    PRICE_DETAIL_URL,
    PRICE_QUERY_URL,
    SCIFINDER_RETRO_API_URL,
)
from .formatter import format_to_test_json, prepend_route_to_response
from .local_api import LocalRetroAPIClient
from .planner import SynthesisPlanner
from .price import PriceClient, StockService
from .schemas import PlanRequest
from .scifinder import SciFinderRetroClient, retro_api_to_list


class ServiceContainer:
    """服务依赖容器，集中管理可复用客户端。"""

    def __init__(self):
        self.price_client = PriceClient(PRICE_DETAIL_URL, PRICE_QUERY_URL, timeout=DEFAULT_PRICE_TIMEOUT)
        self.stock_service = StockService(self.price_client)
        self.local_retro_client = LocalRetroAPIClient(LOCAL_RETRO_API_URL, timeout=DEFAULT_API_TIMEOUT)
        self.scifinder_client = SciFinderRetroClient(SCIFINDER_RETRO_API_URL, timeout=DEFAULT_SCIFINDER_TIMEOUT)

    def configure_request_timeouts(self, request: PlanRequest) -> None:
        self.local_retro_client.set_timeout(request.api_timeout_sec)
        self.price_client.set_timeout(request.price_timeout_sec)
        self.scifinder_client.set_timeout(DEFAULT_SCIFINDER_TIMEOUT)

    def build_planner(self, request: PlanRequest) -> SynthesisPlanner:
        self.configure_request_timeouts(request)
        return SynthesisPlanner(
            api_client=self.local_retro_client,
            stock_service=self.stock_service,
            max_depth=request.max_depth,
            top_paths=request.top_paths,
            top_k_root=request.top_k_root,
            top_k_inner=request.top_k_inner,
            max_nodes=request.max_nodes,
            time_budget_sec=request.time_budget_sec,
            parallel_workers=request.parallel_workers,
            max_reactants_per_step=request.max_reactants_per_step,
        )


services = ServiceContainer()


def build_planner(request: PlanRequest) -> SynthesisPlanner:
    return services.build_planner(request)


def run_local_plan(request: PlanRequest) -> Dict:
    planner = build_planner(request)
    raw_trees = planner.plan(request.smiles)
    return format_to_test_json(raw_trees, stats=planner.stats)


def run_scifinder_plan(request: PlanRequest) -> Dict:
    """按输入 SMILES 先用自己的 detail 接口解析 CAS，再调 SciFinder 路线。"""
    services.price_client.set_timeout(request.price_timeout_sec)
    # cas = services.price_client.resolve_cas(request.smiles)
    # if not cas:
    #     raise ValueError("调用 SciFinder retro 接口需要 CAS，但自己的 detail 接口未查到 CAS")

    return retro_api_to_list(
        cas=request.smiles,
        route_id=0,
        root_structure_id=0,
        timeout=DEFAULT_SCIFINDER_TIMEOUT,
        price_client=services.price_client,
        scifinder_client=services.scifinder_client,
    )


def extract_first_route(scifinder_data: Optional[Dict]) -> Optional[Dict]:
    if not scifinder_data:
        return None
    if isinstance(scifinder_data, dict):
        routes = scifinder_data.get("list") or []
    elif isinstance(scifinder_data, list):
        routes = scifinder_data
    else:
        routes = []
    if not routes:
        return None
    return routes[0]


# def plan_with_local_and_scifinder(request: PlanRequest, timeout_sec: float = DEFAULT_SCIFINDER_TIMEOUT) -> Dict:
#     """同时执行本地路线规划和 SciFinder 远程路线，二者都完成后再返回。"""
#     executor = ThreadPoolExecutor(max_workers=2)
#     try:
#         local_future = executor.submit(run_local_plan, request)
#         scifinder_future = executor.submit(run_scifinder_plan, request)

#         _, not_done = wait(
#             [local_future, scifinder_future],
#             timeout=timeout_sec,
#             return_when=ALL_COMPLETED,
#         )
#         if not_done:
#             for future in not_done:
#                 future.cancel()
#             raise HTTPException(status_code=504, detail=f"规划超时，超过 {int(timeout_sec)}s 仍未完成")

#         formatted_data = local_future.result()
#         scifinder_route = extract_first_route(scifinder_future.result())
#         if scifinder_route:
#             formatted_data = prepend_route_to_response(formatted_data, scifinder_route)
#         return formatted_data

#     except HTTPException:
#         raise
#     except Exception as exc:
#         logging.exception("Planning json error")
#         raise HTTPException(status_code=500, detail=str(exc))
#     finally:
#         executor.shutdown(wait=False, cancel_futures=True)



STATUS_HAS_DATA = 0
STATUS_EMPTY = 1
STATUS_TIMEOUT = 2
STATUS_ERROR = 3

def plan_with_local_and_scifinder(request: PlanRequest, timeout_sec: float = DEFAULT_SCIFINDER_TIMEOUT) -> Dict:
    """同时执行本地路线规划和 SciFinder 远程路线，二者都完成后再返回，并附带错误码。"""
    executor = ThreadPoolExecutor(max_workers=2)
    
    try:
        local_future = executor.submit(run_local_plan, request)
        scifinder_future = executor.submit(run_scifinder_plan, request)

        # 等待完成或超时
        _, not_done = wait(
            [local_future, scifinder_future],
            timeout=timeout_sec,
            return_when=ALL_COMPLETED,
        )

        # 清理超时的任务
        if not_done:
            for future in not_done:
                future.cancel()

        # 解析单个任务的结果与状态
        def parse_future(future, is_local: bool):
            if future in not_done:
                return STATUS_TIMEOUT, None
            
            try:
                res = future.result()
                if is_local:
                    # 本地规划检查 reactions 是否为空
                    reactions = res.get("reactions", []) if isinstance(res, dict) else []
                    if not reactions:
                        return STATUS_EMPTY, res
                    return STATUS_HAS_DATA, res
                else:
                    # SciFinder 规划检查提取后的 route
                    route = extract_first_route(res)
                    if not route:
                        return STATUS_EMPTY, None
                    return STATUS_HAS_DATA, route
            except Exception as exc:
                name = "Local" if is_local else "SciFinder"
                logging.exception(f"{name} planning execution error: {str(exc)}")
                return STATUS_ERROR, None

        # 获取双方状态
        local_state, local_data = parse_future(local_future, is_local=True)
        sf_state, sf_route = parse_future(scifinder_future, is_local=False)

        # 构造基础返回数据 (如果本地报错或超时，提供兜底的空结构)
        if local_data is not None and isinstance(local_data, dict):
            formatted_data = local_data
        else:
            formatted_data = {"reactions": []}

        # 如果 SciFinder 成功返回了数据，将其拼接到结果中
        if sf_state == STATUS_HAS_DATA and sf_route:
            formatted_data = prepend_route_to_response(formatted_data, sf_route)

        # 计算并写入 error_code
        error_code = (local_state * 10) + sf_state
        formatted_data["error_code"] = error_code

        return formatted_data

    except Exception as exc:
        # 捕捉执行器级别等罕见的严重异常
        logging.exception("Critical error in planning executor")
        # 如果彻底崩溃，返回默认空结构和代表最严重的错误码 (33: 双端异常)
        return {"reactions": [], "error_code": 33}
        
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
