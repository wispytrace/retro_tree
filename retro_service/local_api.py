import logging
from typing import Dict, List, Set, Tuple

import requests

from .chemistry import canonicalize_smiles
from .config import DEFAULT_API_TIMEOUT, DEFAULT_CACHE_SIZE
from .http_client import make_session
from .utils import LRUCache, route_quality_score, split_reactants


class LocalRetroAPIClient:
    """本地逆合成候选反应接口封装。"""

    def __init__(self, api_url: str, timeout: float = DEFAULT_API_TIMEOUT):
        self.api_url = api_url
        self.timeout = timeout
        self.session = make_session()
        self._cache = LRUCache(DEFAULT_CACHE_SIZE)

    def set_timeout(self, timeout: float) -> None:
        self.timeout = timeout

    def get_candidates(self, smiles: str, top_k: int = 10) -> List[Dict]:
        canonical = canonicalize_smiles(smiles) or smiles
        cache_key = (canonical, top_k)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        payload = {"smiles": canonical, "top_k": top_k, "materials": ""}
        try:
            response = self.session.post(self.api_url, json=payload, timeout=self.timeout)
            if response.status_code != 200:
                logging.warning("Local retro API returned %s for %s", response.status_code, canonical)
                self._cache.set(cache_key, [])
                return []

            raw_routes = response.json()
            if not isinstance(raw_routes, list):
                logging.warning("Local retro API returned non-list for %s: %s", canonical, type(raw_routes))
                self._cache.set(cache_key, [])
                return []

            filtered = self._filter_routes(raw_routes, canonical)
            self._cache.set(cache_key, filtered)
            return filtered

        except requests.Timeout:
            logging.warning("Local retro API timeout for %s after %.1fs", canonical, self.timeout)
        except Exception as exc:
            logging.error("Local retro API error for %s: %s", canonical, exc)

        self._cache.set(cache_key, [])
        return []

    @staticmethod
    def _filter_routes(raw_routes: List[Dict], canonical_target: str) -> List[Dict]:
        filtered_routes: List[Dict] = []
        seen_signatures: Set[Tuple[str, ...]] = set()

        for route in raw_routes:
            if not isinstance(route, dict):
                continue

            target = canonicalize_smiles(route.get("target_smiles", ""))
            if target and target != canonical_target:
                continue

            clean_reactants: List[str] = []
            valid = True
            for reactant in split_reactants(route.get("materials_smiles", "")):
                canonical_reactant = canonicalize_smiles(reactant)
                if canonical_reactant is None or canonical_reactant == canonical_target:
                    valid = False
                    break
                clean_reactants.append(canonical_reactant)

            if not valid or not clean_reactants:
                continue

            signature = tuple(sorted(clean_reactants))
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)

            new_route = dict(route)
            new_route["target_smiles"] = canonical_target
            new_route["materials_smiles"] = ".".join(clean_reactants)
            new_route["_clean_reactants"] = clean_reactants
            filtered_routes.append(new_route)

        filtered_routes.sort(
            key=lambda r: route_quality_score(r, stock_hits=0, reactant_count=len(r.get("_clean_reactants", []))),
            reverse=True,
        )
        return filtered_routes
