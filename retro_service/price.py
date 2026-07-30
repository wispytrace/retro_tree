import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from .chemistry import canonicalize_smiles
from .config import DEFAULT_CACHE_SIZE, DEFAULT_PRICE_TIMEOUT, PRICE_DEFAULT_UNIT
from .http_client import make_session
from .utils import LRUCache, empty_price_info, normalize_price_unit, safe_float


class PriceClient:
    """价格与库存查询模块。

    所有路线，包括本地逆合成路线和 SciFinder 路线，都只通过这里补充 CAS、价格、库存与统计信息。
    """

    def __init__(self, detail_url: str, price_url: str, timeout: float = DEFAULT_PRICE_TIMEOUT):
        self.detail_url = detail_url
        self.price_url = price_url
        self.timeout = timeout
        self.session = make_session()
        self._info_cache = LRUCache(DEFAULT_CACHE_SIZE)
        self._cas_cache = LRUCache(DEFAULT_CACHE_SIZE)

    def set_timeout(self, timeout: float) -> None:
        self.timeout = timeout

    def resolve_cas(self, smiles: str) -> str:
        """通过自己的 detail 接口按 SMILES 查询 CAS。"""
        canonical = canonicalize_smiles(smiles) or (smiles or "")
        cached = self._cas_cache.get(canonical)
        if cached is not None:
            return cached

        cas_number = ""
        try:
            res = self.session.get(self.detail_url, params={"q": canonical}, timeout=self.timeout)
            if res.status_code == 200:
                data = res.json()
                if data.get("found", False):
                    cas_number = (data.get("data") or {}).get("cas_number", "") or ""
        except Exception as exc:
            logging.debug("CAS resolving failed for %s: %s", canonical, exc)

        self._cas_cache.set(canonical, cas_number)
        return cas_number

    def get_info(self, smiles: str) -> Dict:
        canonical = canonicalize_smiles(smiles) or (smiles or "")
        cached = self._info_cache.get(canonical)
        if cached is not None:
            return cached

        started = time.monotonic()
        info = empty_price_info()

        try:
            cas_number = self.resolve_cas(canonical)
            info["cas"] = cas_number
            if not cas_number:
                return self._cache_and_return(canonical, info, started)

            res_price = self.session.post(self.price_url, json={"cas": cas_number}, timeout=self.timeout)
            if res_price.status_code != 200:
                return self._cache_and_return(canonical, info, started)

            price_list = res_price.json().get("price_list", [])
            best_item, best_price = self._select_best_price(price_list)
            if best_item is not None and best_price is not None:
                raw_unit = (
                    best_item.get("unit")
                    or best_item.get("price_unit")
                    or best_item.get("currency")
                    or best_item.get("currency_unit")
                    or PRICE_DEFAULT_UNIT
                )
                info.update({
                    "price": best_price,
                    "unit": str(raw_unit).strip(),
                    "price_unit": normalize_price_unit(raw_unit, default=PRICE_DEFAULT_UNIT),
                    "spec": best_item.get("spec", ""),
                    "in_stock": True,
                })
        except Exception as exc:
            logging.debug("Price API failed for %s: %s", canonical, exc)

        return self._cache_and_return(canonical, info, started)

    @staticmethod
    def _select_best_price(price_list: List[dict]) -> tuple[Optional[dict], Optional[float]]:
        best_item = None
        best_price = None
        for item in price_list or []:
            if not isinstance(item, dict):
                continue
            price = safe_float(item.get("price"), -1.0)
            if price < 0:
                continue
            if best_price is None or price < best_price:
                best_price = price
                best_item = item
        return best_item, best_price

    def _cache_and_return(self, smiles: str, info: Dict, started: float) -> Dict:
        info["search_time_sec"] = round(time.monotonic() - started, 4)
        self._info_cache.set(smiles, info)
        return info

    def get_price(self, smiles: str) -> float:
        return safe_float(self.get_info(smiles).get("price"), -1.0)

    def warmup_many(self, smiles_list: List[str], workers: int = 8) -> None:
        unique_smiles = list(dict.fromkeys([s for s in smiles_list if s]))
        if not unique_smiles:
            return
        if workers <= 1:
            for s in unique_smiles:
                self.get_info(s)
            return

        with ThreadPoolExecutor(max_workers=min(workers, len(unique_smiles))) as executor:
            futures = [executor.submit(self.get_info, s) for s in unique_smiles]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    pass


class StockService:
    """库存判断模块，内部依赖 PriceClient，避免规划器直接关心价格接口细节。"""

    def __init__(self, price_client: PriceClient):
        self.price_client = price_client

    def get_stock_info(self, smiles: str) -> Dict:
        return self.price_client.get_info(smiles)

    def is_material(self, smiles: str) -> bool:
        return bool(self.get_stock_info(smiles).get("in_stock", False))
