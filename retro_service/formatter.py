from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .config import PRICE_DEFAULT_UNIT
from .utils import normalize_price_unit, safe_float


def format_to_test_json(trees: List[Dict], stats: Optional[Dict] = None) -> Dict:
    """统一输出格式模块：保持原 /api/plan 返回结构不变。"""
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    node_id_counter = 1000

    def get_node_id() -> int:
        nonlocal node_id_counter
        node_id_counter += 1
        return node_id_counter

    def build_structure(node: Dict, parent_id: int) -> Dict:
        chem_id = get_node_id()
        node_smiles = node.get("smiles", "")
        is_material = node.get("type") == "material"
        price = node.get("price", -1.0)
        raw_unit = node.get("unit", "")
        price_unit = node.get("price_unit") or normalize_price_unit(
            raw_unit,
            default=PRICE_DEFAULT_UNIT if price > 0 else "",
        )

        chem_obj = {
            "id": chem_id,
            "type": 0,
            "smiles": node_smiles,
            "cas": node.get("cas", ""),
            "is_chemical": 1,
            "in_stock": 1 if is_material else 0,
            "is_reaction": 0,
            "status": node.get("type", "unknown"),
            "is_resolved": bool(node.get("is_resolved", False)),
            "price": price,
            "unit": raw_unit or price_unit,
            "price_unit": price_unit,
            "spec": node.get("spec", ""),
            "search_time_sec": round(safe_float(node.get("search_time_sec"), 0.0), 4),
            "parent_id": parent_id,
            "created_at": current_time,
            "reactions": [],
            "children": [],
        }

        children_nodes = node.get("children", [])
        if children_nodes:
            rxn_id = get_node_id()
            reactants_smiles = [c.get("smiles", "") for c in children_nodes]
            selected_route = node.get("selected_route", {})
            reactions_list = []

            if selected_route:
                reactions_list.append({
                    "id": get_node_id(),
                    "rs_structure_id": rxn_id,
                    "doi": selected_route.get("doi", ""),
                    "experiments": selected_route.get("conditions", ""),
                    "materials": selected_route.get("materials_smiles", ".".join(reactants_smiles)),
                    "target": selected_route.get("target_smiles", node_smiles),
                    "yields": selected_route.get("yield_val")
                    if selected_route.get("yield_val") is not None
                    else selected_route.get("yield_str", ""),
                    "yields_unit": "%",
                    "publish": selected_route.get("publish", ""),
                    "source_name": selected_route.get("source_name", ""),
                    "is_match": selected_route.get("is_match", False),
                    "is_ai": selected_route.get("is_ai", False),
                    "similarity": selected_route.get("target_score", selected_route.get("similarity", 0.0)),
                    "score": selected_route.get(
                        "chemical_score",
                        selected_route.get("final_score", selected_route.get("score", 0.0)),
                    ),
                    "model_score": selected_route.get("final_score", selected_route.get("score", 0.0)),
                    "chemical_score": selected_route.get("chemical_score"),
                    "chemical_score_status": selected_route.get("chemical_score_status", ""),
                    "chemical_score_coverage": selected_route.get("chemical_score_coverage", 0.0),
                    "chemical_score_coverage_details": selected_route.get(
                        "chemical_score_coverage_details", {}
                    ),
                    "chemical_score_dimensions": selected_route.get("chemical_score_dimensions", {}),
                    "chemical_score_flags": selected_route.get("chemical_score_flags", []),
                    "chemical_score_engine_version": selected_route.get(
                        "chemical_score_engine_version", ""
                    ),
                    "created_at": current_time,
                })

            rxn_obj = {
                "id": rxn_id,
                "type": 1,
                "smiles": f"{node_smiles}>>{'.'.join(reactants_smiles)}",
                "is_chemical": 0,
                "in_stock": 0,
                "is_reaction": 1,
                "parent_id": chem_id,
                "created_at": current_time,
                "reactions": reactions_list,
                "children": [],
            }

            for child in children_nodes:
                rxn_obj["children"].append(build_structure(child, rxn_id))
            chem_obj["children"].append(rxn_obj)

        return chem_obj

    def calculate_metrics(node: Dict) -> Tuple[float, str, float, Dict]:
        total_by_unit: Dict[str, float] = {}
        scores: List[float] = []
        material_count = 0
        priced_material_count = 0
        missing_price_count = 0

        def traverse(current_node: Dict) -> None:
            nonlocal material_count, priced_material_count, missing_price_count

            if current_node.get("type") == "material":
                material_count += 1
                price = safe_float(current_node.get("price"), -1.0)
                if price > 0:
                    unit = current_node.get("price_unit") or current_node.get("unit") or PRICE_DEFAULT_UNIT
                    unit = normalize_price_unit(unit, default=PRICE_DEFAULT_UNIT)
                    total_by_unit[unit] = total_by_unit.get(unit, 0.0) + price
                    priced_material_count += 1
                else:
                    missing_price_count += 1

            route_info = current_node.get("selected_route")
            if route_info:
                step_score = route_info.get(
                    "chemical_score",
                    route_info.get(
                        "score",
                        route_info.get("similarity", route_info.get("target_score", 0.0)),
                    ),
                )
                scores.append(safe_float(step_score, 0.0))

            for child in current_node.get("children", []):
                traverse(child)

        traverse(node)
        total_price, total_price_unit, total_by_unit = summarize_total_by_unit(total_by_unit)
        avg_score = sum(scores) / len(scores) if scores else 0.0
        price_stats = {
            "total_price": total_price,
            "total_price_unit": total_price_unit,
            "total_price_by_unit": total_by_unit,
            "material_count": material_count,
            "priced_material_count": priced_material_count,
            "missing_price_count": missing_price_count,
        }
        return total_price, total_price_unit, round(avg_score, 4), price_stats

    data_list = []
    for idx, tree in enumerate(trees):
        structures = [build_structure(tree, 0)]
        total_price, total_price_unit, avg_score, price_stats = calculate_metrics(tree)
        data_list.append({
            "id": idx + 1,
            "created_at": current_time,
            "total_price": total_price,
            "total_price_unit": total_price_unit,
            "total_price_by_unit": price_stats.get("total_price_by_unit", {}),
            "price_statistics": price_stats,
            "average_score": avg_score,
            "is_resolved": bool(tree.get("is_resolved", False)),
            "structures": structures,
        })

    return {
        "code": 200,
        "msg": "查询逆合成路线成功",
        "data": {
            "list": data_list,
            "page_index": 1,
            "page_rows": len(data_list),
            "data_count": len(data_list),
            "page_count": 1,
            "search_stats": stats or {},
        },
    }


def summarize_total_by_unit(total_by_unit: Dict[str, float]) -> Tuple[float, str, Dict[str, float]]:
    total_by_unit = {k: round(v, 2) for k, v in total_by_unit.items() if safe_float(v, 0.0) > 0}
    if len(total_by_unit) == 1:
        unit = next(iter(total_by_unit.keys()))
        total = next(iter(total_by_unit.values()))
    elif len(total_by_unit) == 0:
        unit = normalize_price_unit(PRICE_DEFAULT_UNIT, default=PRICE_DEFAULT_UNIT)
        total = 0.0
    else:
        unit = "MIXED"
        total = round(sum(total_by_unit.values()), 2)
    return total, unit, total_by_unit


def prepend_route_to_response(formatted_data: Dict, route_item: Dict) -> Dict:
    """把 SciFinder 路线插入 data.list 首位，同时更新分页统计字段。"""
    if not route_item:
        return formatted_data

    if not isinstance(formatted_data, dict):
        return formatted_data

    data = formatted_data.setdefault("data", {})
    if not isinstance(data, dict):
        formatted_data["data"] = data = {"list": []}

    route_list = data.setdefault("list", [])
    if not isinstance(route_list, list):
        data["list"] = route_list = []

    route_item["id"] = 0
    structures = route_item.get("structures")
    if isinstance(structures, list) and structures:
        structures[0]["id"] = 0

    route_list.insert(0, route_item)
    data["page_rows"] = len(route_list)
    data["data_count"] = len(route_list)
    data["page_count"] = 1
    data.setdefault("page_index", 1)
    return formatted_data
