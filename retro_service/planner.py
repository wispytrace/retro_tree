import copy
import time
from typing import Dict, List, Optional, Tuple

from .chemistry import canonicalize_smiles
from .config import DEFAULT_TIME_BUDGET, PRICE_DEFAULT_UNIT
from .local_api import LocalRetroAPIClient
from .price import StockService
from .utils import (
    normalize_price_unit,
    route_priority_key,
    route_quality_score,
    safe_float,
    split_reactants,
)


class SearchStopped(Exception):
    pass


class SynthesisPlanner:
    """本地逆合成路线生成模块。

    只负责搜索与路线树构建，不负责 HTTP 服务、不负责最终 JSON 格式化。
    """

    def __init__(
        self,
        api_client: LocalRetroAPIClient,
        stock_service: StockService,
        max_depth: int = 4,
        top_paths: int = 3,
        top_k_root: int = 5,
        top_k_inner: int = 3,
        max_nodes: int = 500,
        time_budget_sec: float = DEFAULT_TIME_BUDGET,
        parallel_workers: int = 8,
        max_reactants_per_step: int = 5,
    ):
        self.api_client = api_client
        self.stock_service = stock_service
        self.max_depth = max_depth
        self.top_paths = top_paths
        self.top_k_root = top_k_root
        self.top_k_inner = top_k_inner
        self.max_nodes = max_nodes
        self.parallel_workers = parallel_workers
        self.max_reactants_per_step = max_reactants_per_step
        self.deadline = time.monotonic() + time_budget_sec
        self.node_count = 0
        self.memo: Dict[Tuple[str, int], Dict] = {}
        self.stats = {
            "expanded_nodes": 0,
            "memo_hits": 0,
            "timeout_partial": False,
            "max_nodes_hit": False,
            "total_elapsed_sec": 0.0,
            "depth_timing": {},
            "depth_timings": [],
        }

    def plan(self, smiles: str) -> List[Dict]:
        plan_start_time = time.monotonic()
        try:
            smiles = canonicalize_smiles(smiles)
            if smiles is None:
                return [self._terminal_node("", 0, "invalid_smiles")]

            stock = self._stock_node(smiles, depth=0)
            if stock:
                return [stock]

            root_search_time = 0.0
            try:
                search_start_time = time.monotonic()
                candidates = self.api_client.get_candidates(smiles, top_k=self.top_k_root)
                candidates = self._prioritize_routes(candidates)
                root_search_time = time.monotonic() - search_start_time
                self._record_depth_timing(0, root_search_time, smiles)
            except SearchStopped:
                return [self._terminal_node(smiles, 0, "timeout")]

            valid_routes = []
            for route in candidates:
                reactants = route.get("_clean_reactants") or split_reactants(route.get("materials_smiles", ""))
                if 0 < len(reactants) <= self.max_reactants_per_step and smiles not in set(reactants):
                    valid_routes.append((reactants, route))

            if not valid_routes:
                info = self.stock_service.get_stock_info(smiles)
                return [self._terminal_node(
                    smiles,
                    0,
                    "dead_end",
                    info.get("price", -1.0),
                    unit=info.get("unit", ""),
                    price_unit=info.get("price_unit", ""),
                    spec=info.get("spec", ""),
                    cas=info.get("cas", ""),
                    search_time_sec=root_search_time,
                )]

            resolved_trees: List[Dict] = []
            unresolved_trees: List[Dict] = []

            for rank, (reactants, route) in enumerate(valid_routes, start=1):
                try:
                    self._check_budget()
                    children_nodes = []
                    all_resolved = True
                    ancestors = frozenset([smiles])

                    for reactant in reactants:
                        child = self._recursive_plan(reactant, depth=1, ancestors=ancestors)
                        children_nodes.append(child)
                        if not child.get("is_resolved", False):
                            all_resolved = False
                            break

                    if len(children_nodes) < len(reactants):
                        expanded = {c.get("smiles") for c in children_nodes}
                        for reactant in reactants:
                            if reactant not in expanded:
                                children_nodes.append(self._terminal_node(reactant, 1, "pruned"))

                    root_node = {
                        "smiles": smiles,
                        "type": "intermediate",
                        "children": children_nodes,
                        "depth": 0,
                        "route_rank": rank,
                        "price": -1.0,
                        "unit": "",
                        "price_unit": "",
                        "spec": "",
                        "search_time_sec": round(root_search_time, 4),
                        "selected_route": route,
                        "is_resolved": all_resolved,
                    }
                    if all_resolved:
                        resolved_trees.append(root_node)
                    else:
                        unresolved_trees.append(root_node)

                    if len(resolved_trees) >= self.top_paths:
                        break
                except SearchStopped:
                    break

            final_trees = resolved_trees + unresolved_trees[: max(0, self.top_paths - len(resolved_trees))]
            if not final_trees:
                node_type = "timeout" if self.stats["timeout_partial"] else "max_nodes"
                final_trees = [self._terminal_node(smiles, 0, node_type)]
            return final_trees[: self.top_paths]
        finally:
            self.stats["total_elapsed_sec"] = round(time.monotonic() - plan_start_time, 4)
            self._finalize_depth_timing()

    def _recursive_plan(self, smiles: str, depth: int, ancestors: frozenset) -> Dict:
        self._check_budget()
        canonical = canonicalize_smiles(smiles)
        if canonical is None:
            return self._terminal_node(smiles, depth, "invalid_smiles")
        smiles = canonical

        stock = self._stock_node(smiles, depth)
        if stock:
            return stock

        if depth >= self.max_depth:
            info = self.stock_service.get_stock_info(smiles)
            return self._terminal_node(
                smiles,
                depth,
                "max_depth",
                info.get("price", -1.0),
                unit=info.get("unit", ""),
                price_unit=info.get("price_unit", ""),
                spec=info.get("spec", ""),
                cas=info.get("cas", ""),
            )

        if smiles in ancestors:
            return self._terminal_node(smiles, depth, "cycle")

        remaining_depth = self.max_depth - depth
        memo_key = (smiles, remaining_depth)
        if memo_key in self.memo:
            self.stats["memo_hits"] += 1
            cached = copy.deepcopy(self.memo[memo_key])
            cached["depth"] = depth
            cached["from_memo"] = True
            cached["search_time_sec"] = 0.0
            return cached

        self.node_count += 1
        self.stats["expanded_nodes"] += 1
        self._check_budget()

        search_start_time = time.monotonic()
        candidates = self.api_client.get_candidates(smiles, top_k=self.top_k_inner)
        if candidates:
            candidates = self._prioritize_routes(candidates)
        search_time_sec = time.monotonic() - search_start_time
        self._record_depth_timing(depth, search_time_sec, smiles)

        if not candidates:
            info = self.stock_service.get_stock_info(smiles)
            node = self._terminal_node(
                smiles,
                depth,
                "dead_end",
                info.get("price", -1.0),
                unit=info.get("unit", ""),
                price_unit=info.get("price_unit", ""),
                spec=info.get("spec", ""),
                cas=info.get("cas", ""),
                search_time_sec=search_time_sec,
            )
            self.memo[memo_key] = copy.deepcopy(node)
            return node

        best_node: Optional[Dict] = None
        new_ancestors = frozenset(set(ancestors) | {smiles})

        for rank, route in enumerate(candidates, start=1):
            self._check_budget()
            reactants = route.get("_clean_reactants") or split_reactants(route.get("materials_smiles", ""))
            if not reactants or len(reactants) > self.max_reactants_per_step:
                continue
            if smiles in set(reactants) or any(r in ancestors for r in reactants):
                continue

            children_nodes = []
            all_resolved = True
            for reactant in reactants:
                child = self._recursive_plan(reactant, depth + 1, new_ancestors)
                children_nodes.append(child)
                if not child.get("is_resolved", False):
                    all_resolved = False
                    break

            if len(children_nodes) < len(reactants):
                expanded = {c.get("smiles") for c in children_nodes}
                for reactant in reactants:
                    if reactant not in expanded:
                        children_nodes.append(self._terminal_node(reactant, depth + 1, "pruned"))

            candidate_node = {
                "smiles": smiles,
                "type": "intermediate",
                "route_rank": rank,
                "selected_route": route,
                "children": children_nodes,
                "depth": depth,
                "price": -1.0,
                "unit": "",
                "price_unit": "",
                "spec": "",
                "search_time_sec": round(search_time_sec, 4),
                "is_resolved": all_resolved,
            }

            if best_node is None or self._node_score(candidate_node) > self._node_score(best_node):
                best_node = candidate_node
            if all_resolved:
                best_node = candidate_node
                break

        if best_node is None:
            info = self.stock_service.get_stock_info(smiles)
            best_node = self._terminal_node(
                smiles,
                depth,
                "dead_end",
                info.get("price", -1.0),
                unit=info.get("unit", ""),
                price_unit=info.get("price_unit", ""),
                spec=info.get("spec", ""),
                cas=info.get("cas", ""),
                search_time_sec=search_time_sec,
            )

        self.memo[memo_key] = copy.deepcopy(best_node)
        return best_node

    def _stock_node(self, smiles: str, depth: int) -> Optional[Dict]:
        info = self.stock_service.get_stock_info(smiles)
        if info.get("in_stock", False):
            return {
                "smiles": smiles,
                "type": "material",
                "children": [],
                "depth": depth,
                "route_rank": 0,
                "price": info.get("price", -1.0),
                "unit": info.get("unit", ""),
                "price_unit": info.get("price_unit", normalize_price_unit(info.get("unit", ""), default=PRICE_DEFAULT_UNIT)),
                "spec": info.get("spec", ""),
                "cas": info.get("cas", ""),
                "search_time_sec": 0.0,
                "is_resolved": True,
            }
        return None

    @staticmethod
    def _terminal_node(
        smiles: str,
        depth: int,
        node_type: str,
        price: float = -1.0,
        unit: str = "",
        price_unit: str = "",
        spec: str = "",
        cas: str = "",
        search_time_sec: float = 0.0,
    ) -> Dict:
        normalized_unit = price_unit or normalize_price_unit(unit, default=PRICE_DEFAULT_UNIT if price > 0 else "")
        return {
            "smiles": smiles,
            "type": node_type,
            "children": [],
            "depth": depth,
            "route_rank": 0,
            "price": price,
            "unit": unit or normalized_unit,
            "price_unit": normalized_unit,
            "spec": spec,
            "cas": cas,
            "search_time_sec": round(float(search_time_sec or 0.0), 4),
            "is_resolved": False,
        }

    def _prioritize_routes(self, routes: List[Dict]) -> List[Dict]:
        routes = [route for route in routes if route.get("is_match") is True]
        all_reactants: List[str] = []
        for route in routes:
            all_reactants.extend(route.get("_clean_reactants") or split_reactants(route.get("materials_smiles", "")))
        self.stock_service.price_client.warmup_many(all_reactants, workers=self.parallel_workers)

        def priority(route: Dict) -> tuple[int, float, float]:
            reactants = route.get("_clean_reactants") or split_reactants(route.get("materials_smiles", ""))
            stock_hits = sum(1 for s in reactants if self.stock_service.is_material(s))
            return route_priority_key(
                route,
                stock_hits=stock_hits,
                reactant_count=len(reactants),
            )

        return sorted(routes, key=priority)

    def _record_depth_timing(self, depth: int, elapsed_sec: float, smiles: str = "") -> None:
        depth_key = str(depth)
        bucket = self.stats.setdefault("depth_timing", {}).setdefault(depth_key, {
            "depth": depth,
            "search_count": 0,
            "search_time_sec": 0.0,
            "max_search_time_sec": 0.0,
            "avg_search_time_sec": 0.0,
            "smiles_samples": [],
        })
        elapsed_sec = max(0.0, float(elapsed_sec))
        bucket["search_count"] += 1
        bucket["search_time_sec"] += elapsed_sec
        bucket["max_search_time_sec"] = max(bucket["max_search_time_sec"], elapsed_sec)
        bucket["avg_search_time_sec"] = bucket["search_time_sec"] / max(bucket["search_count"], 1)
        if smiles and len(bucket["smiles_samples"]) < 5 and smiles not in bucket["smiles_samples"]:
            bucket["smiles_samples"].append(smiles)

    def _finalize_depth_timing(self) -> None:
        depth_timing = self.stats.get("depth_timing", {}) or {}
        depth_timings = []
        for item in sorted(depth_timing.values(), key=lambda x: int(x.get("depth", 0))):
            item["search_time_sec"] = round(float(item.get("search_time_sec", 0.0)), 4)
            item["max_search_time_sec"] = round(float(item.get("max_search_time_sec", 0.0)), 4)
            item["avg_search_time_sec"] = round(float(item.get("avg_search_time_sec", 0.0)), 4)
            depth_timings.append(item)
        self.stats["depth_timings"] = depth_timings

    def _check_budget(self) -> None:
        if time.monotonic() > self.deadline:
            self.stats["timeout_partial"] = True
            raise SearchStopped("time budget exceeded")
        if self.node_count >= self.max_nodes:
            self.stats["max_nodes_hit"] = True
            raise SearchStopped("max_nodes exceeded")

    @staticmethod
    def _node_score(node: Dict) -> float:
        if node.get("is_resolved"):
            return 1_000_000.0
        score = 0.0
        stack = [node]
        while stack:
            cur = stack.pop()
            if cur.get("type") == "material":
                score += 10.0
            if cur.get("type") in {"dead_end", "cycle", "max_depth", "timeout"}:
                score -= 2.0
            route = cur.get("selected_route") or {}
            score += route_quality_score(route)
            score += safe_float(route.get("chemical_score"), 0.0) / 10.0
            stack.extend(cur.get("children", []))
        return score
