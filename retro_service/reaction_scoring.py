"""Adapter between route candidates and the local chemical-score engine."""

from __future__ import annotations

from typing import Any, Protocol

from chemical_score import ReactionEvaluator, get_default_evaluator

from .utils import LRUCache


class CandidateReactionScorer(Protocol):
    def score(self, reaction_smiles: str) -> dict[str, Any]: ...


class ChemicalReactionScorer:
    """Evaluate candidate reactions once and retain a compact ranking payload."""

    def __init__(
        self,
        evaluator: ReactionEvaluator | None = None,
        *,
        cache_size: int = 10_000,
        warm_up: bool = True,
    ) -> None:
        self.evaluator = evaluator or get_default_evaluator()
        self._cache = LRUCache(cache_size)
        if warm_up:
            self.evaluator.warm_up()

    def score(self, reaction_smiles: str) -> dict[str, Any]:
        cached = self._cache.get(reaction_smiles)
        if cached is not None:
            return cached

        result = self.evaluator.evaluate(*self._split(reaction_smiles))
        if result["status"] == "invalid_input" or result["score"] is None:
            raise ValueError(
                "chemical-score rejected candidate reaction: "
                + "; ".join(str(item) for item in result.get("errors", []))
            )

        dimensions = {
            node["id"]: {
                "score": node.get("score"),
                "status": node.get("status"),
                "effective_weight": node.get("effective_weight", 0.0),
            }
            for node in result["score_tree"].get("children", [])
        }
        compact = {
            "chemical_score": float(result["score"]),
            "chemical_score_status": result["status"],
            "chemical_score_coverage": result["coverage"],
            "chemical_score_coverage_details": result.get("coverage_details", {}),
            "chemical_score_dimensions": dimensions,
            "chemical_score_flags": result.get("flags", []),
            "chemical_score_engine_version": result.get("engine_version", ""),
        }
        self._cache.set(reaction_smiles, compact)
        return compact

    @staticmethod
    def _split(reaction_smiles: str) -> tuple[str, str, str | None]:
        parts = reaction_smiles.split(">")
        if len(parts) != 3 or not parts[0].strip() or not parts[2].strip():
            raise ValueError(
                "candidate reaction must use reactants>agents>product format"
            )
        reactants, agents, product = (part.strip() for part in parts)
        return reactants, product, agents or None
