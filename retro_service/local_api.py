import logging
from typing import Dict, List, Tuple

import requests

from .chemistry import canonicalize_smiles
from .config import (
    DEFAULT_API_TIMEOUT,
    DEFAULT_CACHE_SIZE,
    LOCAL_CANDIDATE_FETCH_MULTIPLIER,
)
from .http_client import make_session
from .reaction_scoring import CandidateReactionScorer, ChemicalReactionScorer
from .utils import LRUCache, route_priority_key, split_reactants


class LocalRetroAPIClient:
    """本地逆合成候选反应接口封装。"""

    def __init__(
        self,
        api_url: str,
        timeout: float = DEFAULT_API_TIMEOUT,
        reaction_scorer: CandidateReactionScorer | None = None,
    ):
        self.api_url = api_url
        self.timeout = timeout
        self.session = make_session()
        self._cache = LRUCache(DEFAULT_CACHE_SIZE)
        self.reaction_scorer = reaction_scorer or ChemicalReactionScorer()

    def set_timeout(self, timeout: float) -> None:
        self.timeout = timeout

    def get_candidates(self, smiles: str, top_k: int = 10) -> List[Dict]:
        canonical = canonicalize_smiles(smiles) or smiles
        cache_key = (canonical, top_k)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        fetch_top_k = max(top_k, top_k * LOCAL_CANDIDATE_FETCH_MULTIPLIER)
        payload = {"smiles": canonical, "top_k": fetch_top_k, "materials": ""}
        try:
            response = self.session.post(self.api_url, json=payload, timeout=self.timeout)
            if response.status_code != 200:
                logging.warning("Local retro API returned %s for %s", response.status_code, canonical)
                self._cache.set(cache_key, [])
                return []

            response_payload = response.json()
            raw_routes = (
                response_payload.get("candidates")
                if isinstance(response_payload, dict)
                else response_payload
            )
            if not isinstance(raw_routes, list):
                logging.warning(
                    "Local retro API returned non-list candidates for %s: %s",
                    canonical,
                    type(raw_routes),
                )
                self._cache.set(cache_key, [])
                return []

            filtered = self._filter_routes(raw_routes, canonical)[:top_k]
            self._cache.set(cache_key, filtered)
            return filtered

        except requests.Timeout:
            logging.warning("Local retro API timeout for %s after %.1fs", canonical, self.timeout)
        except Exception as exc:
            logging.error("Local retro API error for %s: %s", canonical, exc)

        self._cache.set(cache_key, [])
        return []

    def _filter_routes(
        self, raw_routes: List[Dict], canonical_target: str
    ) -> List[Dict]:
        routes_by_signature: Dict[Tuple[str, ...], Dict] = {}

        for route in raw_routes:
            if not isinstance(route, dict):
                continue
            if route.get("is_match") is not True:
                continue

            reaction_smiles = str(route.get("reaction_smiles") or "").strip()
            reaction_parts = reaction_smiles.split(">")
            reaction_materials = reaction_parts[0] if len(reaction_parts) == 3 else ""
            reaction_target = reaction_parts[2] if len(reaction_parts) == 3 else ""
            target = canonicalize_smiles(
                route.get("target_smiles", "") or reaction_target
            )
            if target != canonical_target:
                continue

            clean_reactants: List[str] = []
            valid = True
            materials = route.get("materials_smiles", "") or reaction_materials
            for reactant in split_reactants(materials):
                canonical_reactant = canonicalize_smiles(reactant)
                if canonical_reactant is None or canonical_reactant == canonical_target:
                    valid = False
                    break
                clean_reactants.append(canonical_reactant)

            if not valid or not clean_reactants:
                continue

            signature = tuple(sorted(clean_reactants))
            new_route = dict(route)
            new_route["is_match"] = True
            new_route["target_smiles"] = canonical_target
            new_route["materials_smiles"] = ".".join(clean_reactants)
            new_route["reaction_smiles"] = (
                f"{new_route['materials_smiles']}>>{canonical_target}"
            )
            new_route["_clean_reactants"] = clean_reactants
            try:
                new_route.update(
                    self.reaction_scorer.score(new_route["reaction_smiles"])
                )
            except Exception as exc:
                logging.warning(
                    "Chemical scoring rejected candidate %s: %s",
                    new_route["reaction_smiles"],
                    exc,
                )
                continue

            existing = routes_by_signature.get(signature)
            if existing is None or route_priority_key(
                new_route,
                reactant_count=len(clean_reactants),
            ) < route_priority_key(
                existing,
                reactant_count=len(existing.get("_clean_reactants", [])),
            ):
                routes_by_signature[signature] = new_route

        return sorted(
            routes_by_signature.values(),
            key=lambda route: route_priority_key(
                route,
                reactant_count=len(route.get("_clean_reactants", [])),
            ),
        )
