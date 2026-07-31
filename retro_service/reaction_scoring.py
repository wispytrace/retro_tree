"""HTTP adapter between route candidates and the Chemical Score service."""

from __future__ import annotations

from typing import Any, Protocol

import requests
from requests import Session

from .config import (
    CHEMICAL_SCORE_API_TIMEOUT,
    CHEMICAL_SCORE_API_URL,
    DEFAULT_CACHE_SIZE,
)
from .http_client import make_session
from .utils import LRUCache


class CandidateReactionScorer(Protocol):
    def score(self, reaction_smiles: str) -> dict[str, Any]: ...


class ChemicalScoreServiceError(RuntimeError):
    """Raised when the Chemical Score HTTP service cannot score a reaction."""


class ChemicalReactionScorer:
    """Call Chemical Score over HTTP and retain a compact ranking payload."""

    def __init__(
        self,
        api_url: str = CHEMICAL_SCORE_API_URL,
        *,
        timeout: float = CHEMICAL_SCORE_API_TIMEOUT,
        cache_size: int = DEFAULT_CACHE_SIZE,
        session: Session | None = None,
    ) -> None:
        self.api_url = api_url
        self.timeout = timeout
        self.session = session or make_session()
        self._cache = LRUCache(cache_size)

    def score(self, reaction_smiles: str) -> dict[str, Any]:
        reaction_smiles = reaction_smiles.strip()
        if not reaction_smiles:
            raise ValueError("reaction_smiles must not be empty")

        cached = self._cache.get(reaction_smiles)
        if cached is not None:
            return cached

        try:
            response = self.session.post(
                self.api_url,
                json={"reaction_smiles": reaction_smiles},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ChemicalScoreServiceError(
                f"Chemical Score service request failed: {exc}"
            ) from exc

        if response.status_code != 200:
            detail = self._response_detail(response)
            raise ChemicalScoreServiceError(
                f"Chemical Score service returned HTTP {response.status_code}: {detail}"
            )

        try:
            result = response.json()
        except ValueError as exc:
            raise ChemicalScoreServiceError(
                "Chemical Score service returned invalid JSON"
            ) from exc

        compact = self._compact_result(result)
        self._cache.set(reaction_smiles, compact)
        return compact

    @staticmethod
    def _compact_result(result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise ChemicalScoreServiceError(
                "Chemical Score response must be a JSON object"
            )
        if result.get("status") == "invalid_input" or result.get("score") is None:
            errors = result.get("errors") or ["score is missing"]
            raise ChemicalScoreServiceError(
                "Chemical Score rejected candidate reaction: "
                + "; ".join(str(item) for item in errors)
            )

        score_tree = result.get("score_tree")
        if not isinstance(score_tree, dict):
            raise ChemicalScoreServiceError(
                "Chemical Score response is missing score_tree"
            )

        dimensions = {
            node["id"]: {
                "score": node.get("score"),
                "status": node.get("status"),
                "effective_weight": node.get("effective_weight", 0.0),
            }
            for node in score_tree.get("children", [])
            if isinstance(node, dict) and node.get("id")
        }
        return {
            "chemical_score": float(result["score"]),
            "chemical_score_status": str(result.get("status", "")),
            "chemical_score_coverage": result.get("coverage", 0.0),
            "chemical_score_coverage_details": result.get("coverage_details", {}),
            "chemical_score_dimensions": dimensions,
            "chemical_score_flags": result.get("flags", []),
            "chemical_score_engine_version": result.get("engine_version", ""),
        }

    @staticmethod
    def _response_detail(response: Any) -> str:
        try:
            payload = response.json()
        except ValueError:
            return str(getattr(response, "text", "") or "no response body")
        if isinstance(payload, dict):
            return str(payload.get("detail") or payload)
        return str(payload)
