import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from .config import DEFAULT_SCIFINDER_TIMEOUT, PRICE_DEFAULT_UNIT, SCIFINDER_API_KEY, SCIFINDER_RETRO_API_URL
from .formatter import summarize_total_by_unit
from .http_client import make_session
from .price import PriceClient
from .utils import IdGenerator, normalize_price_unit, safe_float


class SciFinderRetroClient:
    """远程 SciFinder 路线接口封装。"""

    def __init__(
        self,
        api_url: str = SCIFINDER_RETRO_API_URL,
        api_key: Optional[str] = None,
        timeout: float = DEFAULT_SCIFINDER_TIMEOUT,
    ):
        self.api_url = api_url
        self.api_key = api_key or SCIFINDER_API_KEY
        self.timeout = timeout
        self.session = make_session(pool_size=8)

    def set_timeout(self, timeout: float) -> None:
        self.timeout = timeout

    def call(self, cas: str, *, rdf: bool = True, conditions: bool = True, references: bool = True) -> Dict[str, Any]:
        api_key = self.api_key or os.getenv("SCIFINDER_API_KEY") or os.getenv("RETRO_API_KEY")
        if not api_key:
            raise ValueError("请设置环境变量 SCIFINDER_API_KEY 或 RETRO_API_KEY")

        payload = {
            "cas": cas,
            "rdf": rdf,
            "conditions": conditions,
            "references": references,
        }
        headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json",
        }
        resp = self.session.post(self.api_url, headers=headers, json=payload, timeout=self.timeout)

        try:
            data = resp.json()
        except Exception:
            raise RuntimeError(f"SciFinder 接口返回不是合法 JSON，status={resp.status_code}, text={resp.text[:1000]}")

        if resp.status_code != 200:
            raise RuntimeError(
                f"SciFinder 接口请求失败，status={resp.status_code}, "
                f"response={json.dumps(data, ensure_ascii=False)[:2000]}"
            )
        return data


def format_created_at(ts: Optional[Any] = None) -> str:
    try:
        if ts:
            return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def split_forward_rxn_smiles(rxn_smiles: str) -> Tuple[str, str]:
    if not rxn_smiles or ">>" not in rxn_smiles:
        return "", ""
    left, right = rxn_smiles.split(">>", 1)
    return left.strip(), right.strip()


def to_retro_rxn_smiles(forward_rxn_smiles: str) -> str:
    materials, target = split_forward_rxn_smiles(forward_rxn_smiles)
    if not materials or not target:
        return forward_rxn_smiles
    return f"{target}>>{materials}"


class SciFinderRouteFormatter:
    """SciFinder 原始返回 -> 目标 list 单条 route。

    注意：价格、库存与价格统计只使用传入的 PriceClient，不使用 SciFinder 返回里的 overall_price/building_blocks。
    """

    def __init__(self, price_client: PriceClient, source_name: str = "SciFinder"):
        self.price_client = price_client
        self.source_name = source_name

    def convert(
        self,
        raw: Dict[str, Any],
        *,
        route_id: int = 0,
        root_structure_id: int = 0,
        reaction_smiles_mode: str = "retro",
    ) -> Dict[str, List[Dict[str, Any]]]:
        created_at = format_created_at(raw.get("ts"))
        root_structures = (raw.get("retro_route") or {}).get("structures") or []
        if not root_structures:
            raise ValueError("SciFinder 返回中 retro_route.structures 为空，无法转换")

        id_gen = IdGenerator(start=root_structure_id + 1)

        def convert_reaction_detail(reaction_node_id: int, node: Dict[str, Any]) -> Dict[str, Any]:
            rxn_smiles_forward = node.get("smiles") or ""
            materials, target = split_forward_rxn_smiles(rxn_smiles_forward)
            reaction_info = node.get("reaction") or {}
            return {
                "id": id_gen.next(),
                "rs_structure_id": reaction_node_id,
                "doi": reaction_info.get("doi") or "",
                "experiments": reaction_info.get("conditions") or "",
                "materials": materials,
                "target": target,
                "yields": safe_float(reaction_info.get("yield"), 0.0),
                "yields_unit": "%",
                "publish": reaction_info.get("publish") or "",
                "source_name": self.source_name,
                "is_ai": False,
                "similarity": 1,
                "score": 1.0,
                "reference": reaction_info.get("reference") or "",
                "created_at": created_at,
            }

        def convert_node(node: Dict[str, Any], parent_id: int, *, forced_id: Optional[int] = None) -> Dict[str, Any]:
            node_type = int(node.get("type", 0))
            node_id = forced_id if forced_id is not None else id_gen.next()
            smiles = node.get("smiles") or ""

            if node_type == 1:
                node_smiles = to_retro_rxn_smiles(smiles) if reaction_smiles_mode == "retro" else smiles
                converted = {
                    "id": node_id,
                    "type": 1,
                    "smiles": node_smiles,
                    "is_chemical": 0,
                    "in_stock": 0,
                    "is_reaction": 1,
                    "parent_id": parent_id,
                    "created_at": created_at,
                    "reactions": [convert_reaction_detail(node_id, node)],
                    "children": [],
                }
            else:
                children = node.get("children") or []
                is_leaf = len(children) == 0
                price_info = self.price_client.get_info(smiles) if smiles else {}
                price = safe_float(price_info.get("price"), -1.0)
                in_stock = bool(price_info.get("in_stock", False))
                raw_unit = price_info.get("unit", "")
                price_unit = price_info.get("price_unit") or normalize_price_unit(
                    raw_unit,
                    default=PRICE_DEFAULT_UNIT if price > 0 else "",
                )

                # SciFinder 的叶子节点代表路线起始物；库存/价格只由自己的价格接口决定。
                status = "material" if is_leaf else "intermediate"
                converted = {
                    "id": node_id,
                    "type": 0,
                    "smiles": smiles,
                    "cas": price_info.get("cas") or node.get("cas_number") or "",
                    "is_chemical": 1,
                    "in_stock": 1 if in_stock else 0,
                    "is_reaction": 0,
                    "status": status,
                    "is_resolved": True,
                    "price": price,
                    "unit": raw_unit or price_unit,
                    "price_unit": price_unit,
                    "spec": price_info.get("spec", ""),
                    "search_time_sec": price_info.get("search_time_sec", 0),
                    "parent_id": parent_id,
                    "created_at": created_at,
                    "reactions": [],
                    "children": [],
                }

            for child in node.get("children") or []:
                converted["children"].append(convert_node(child, parent_id=node_id))
            return converted

        root = convert_node(root_structures[0], parent_id=0, forced_id=root_structure_id)
        total_price, total_price_unit, price_stats = self.calculate_price_statistics(root)
        average_score = self.calculate_average_score(raw)

        route_item = {
            "id": route_id,
            "created_at": created_at,
            "cas": raw.get("cas") or "",
            "formula": raw.get("formula") or "",
            "status": raw.get("status") or "",
            "plan_url": raw.get("plan_url") or "",
            "screenshot": raw.get("screenshot") or "",
            "rdf_file": raw.get("rdf_file") or "",
            "total_price": total_price,
            "total_price_unit": total_price_unit,
            "total_price_by_unit": price_stats.get("total_price_by_unit", {}),
            "price_statistics": price_stats,
            "average_score": average_score,
            "is_resolved": bool(raw.get("ok")) and str(raw.get("status", "")).lower() == "complete",
            "structures": [root],
        }
        return {"list": [route_item]}

    @staticmethod
    def calculate_average_score(raw: Dict[str, Any]) -> float:
        scores = []
        for reaction in raw.get("reactions") or []:
            cond = reaction.get("conditions") or {}
            if cond.get("score") is not None:
                scores.append(safe_float(cond.get("score"), 0.0))
        return round(sum(scores) / len(scores), 4) if scores else 1.0

    @staticmethod
    def calculate_price_statistics(root: Dict[str, Any]) -> Tuple[float, str, Dict[str, Any]]:
        total_by_unit: Dict[str, float] = {}
        material_count = 0
        priced_material_count = 0
        missing_price_count = 0

        def traverse(node: Dict[str, Any]) -> None:
            nonlocal material_count, priced_material_count, missing_price_count
            if node.get("type") == 0:
                children = node.get("children") or []
                is_leaf_chemical = len(children) == 0
                if is_leaf_chemical:
                    material_count += 1
                    price = safe_float(node.get("price"), -1.0)
                    if price > 0:
                        unit = node.get("price_unit") or node.get("unit") or PRICE_DEFAULT_UNIT
                        unit = normalize_price_unit(unit, default=PRICE_DEFAULT_UNIT)
                        total_by_unit[unit] = total_by_unit.get(unit, 0.0) + price
                        priced_material_count += 1
                    else:
                        missing_price_count += 1
            for child in node.get("children") or []:
                traverse(child)

        traverse(root)
        total_price, total_price_unit, total_by_unit = summarize_total_by_unit(total_by_unit)
        return total_price, total_price_unit, {
            "total_price": total_price,
            "total_price_unit": total_price_unit,
            "total_price_by_unit": total_by_unit,
            "material_count": material_count,
            "priced_material_count": priced_material_count,
            "missing_price_count": missing_price_count,
        }


def retro_api_to_list(
    cas: str,
    *,
    api_key = "j4wwrww5Uy28aKcPQofKsGmX6EbOFRF7pgCI2VJb9-s",
    route_id: int = 0,
    root_structure_id: int = 0,
    timeout: float = DEFAULT_SCIFINDER_TIMEOUT,
    save_raw_path: Optional[str] = None,
    save_converted_path: Optional[str] = None,
    price_client: Optional[PriceClient] = None,
    scifinder_client: Optional[SciFinderRetroClient] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    if price_client is None:
        raise ValueError("retro_api_to_list 必须传入 price_client，SciFinder 路线价格统计不能使用远程返回价格")

    client = scifinder_client or SciFinderRetroClient(api_key=api_key, timeout=timeout)
    raw = client.call(cas, rdf=True, conditions=True, references=True)

    with open("save_raw_path.json", "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)

    converted = SciFinderRouteFormatter(price_client).convert(
        raw,
        route_id=route_id,
        root_structure_id=root_structure_id,
        reaction_smiles_mode="retro",
    )

    if save_converted_path:
        with open(save_converted_path, "w", encoding="utf-8") as f:
            json.dump(converted, f, ensure_ascii=False, indent=2)

    return converted
