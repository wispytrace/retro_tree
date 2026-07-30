import copy
import threading
from collections import OrderedDict
from typing import Any, List

from .config import DEFAULT_CACHE_SIZE, PRICE_DEFAULT_UNIT


class LRUCache:
    """线程安全轻量 LRU，用于 API 查询缓存。"""

    def __init__(self, max_size: int = DEFAULT_CACHE_SIZE):
        self.max_size = max_size
        self._data: OrderedDict[Any, Any] = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            if key not in self._data:
                return default
            value = self._data.pop(key)
            self._data[key] = value
            return copy.deepcopy(value)

    def set(self, key: Any, value: Any) -> None:
        with self._lock:
            if key in self._data:
                self._data.pop(key)
            self._data[key] = copy.deepcopy(value)
            while len(self._data) > self.max_size:
                self._data.popitem(last=False)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", "").strip())
    except Exception:
        return default


def split_reactants(materials_smiles: str) -> List[str]:
    return [x.strip() for x in (materials_smiles or "").split(".") if x.strip()]


def normalize_price_unit(unit: Any, default: str = "") -> str:
    """统一价格单位字段，避免 ￥、¥、元、RMB 等混用。"""
    text = str(unit or "").strip()
    if not text:
        return default

    compact = text.replace(" ", "")
    lower = compact.lower()

    if (
        lower in {"cny", "rmb", "cn¥", "¥", "￥"}
        or "人民币" in compact
        or "元" in compact
        or "¥" in compact
        or "￥" in compact
    ):
        return "CNY"
    if lower in {"usd", "us$", "$"} or "美元" in compact or "$" in compact:
        return "USD"
    if lower in {"eur", "€"} or "欧元" in compact or "€" in compact:
        return "EUR"
    if compact.isalpha() and len(compact) <= 5:
        return compact.upper()
    return compact


def route_quality_score(route: dict, stock_hits: int = 0, reactant_count: int = 1) -> float:
    """候选反应排序分：模型分数 + 现货命中 + 收率 - 反应物数量惩罚。"""
    model_score = max(
        safe_float(route.get("final_score"), 0.0),
        safe_float(route.get("score"), 0.0),
        safe_float(route.get("target_score"), 0.0),
        safe_float(route.get("similarity"), 0.0),
    )
    y = safe_float(route.get("yield_val"), 0.0)
    if y <= 0:
        y = safe_float(str(route.get("yield_str", "")).replace("%", ""), 0.0)
    return model_score * 10.0 + stock_hits * 3.0 + min(y, 100.0) / 100.0 - reactant_count * 0.15


class IdGenerator:
    def __init__(self, start: int = 0):
        self.cur = start

    def next(self) -> int:
        value = self.cur
        self.cur += 1
        return value


def empty_price_info() -> dict:
    unit = normalize_price_unit(PRICE_DEFAULT_UNIT, default=PRICE_DEFAULT_UNIT)
    return {
        "price": -1.0,
        "unit": PRICE_DEFAULT_UNIT,
        "price_unit": unit,
        "spec": "",
        "cas": "",
        "in_stock": False,
        "search_time_sec": 0.0,
    }
