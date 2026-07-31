from __future__ import annotations

from retro_service.chemistry import canonicalize_smiles
from retro_service.formatter import format_to_test_json
from retro_service.local_api import LocalRetroAPIClient
from retro_service.planner import SynthesisPlanner
from retro_service.reaction_scoring import ChemicalReactionScorer
from retro_service.utils import route_priority_key


class StubReactionScorer:
    def score(self, reaction_smiles: str) -> dict:
        scores = {
            "C.CO>>CCO": 85.0,
            "CC.O>>CCO": 50.0,
            "C.CC>>CCO": 99.0,
            "CO.C>>CCO": 100.0,
        }
        return {
            "chemical_score": scores[reaction_smiles],
            "chemical_score_status": "success",
            "chemical_score_coverage": 1.0,
            "chemical_score_dimensions": {},
            "chemical_score_flags": [],
        }


class FakeResponse:
    status_code = 200

    def json(self):
        return {"candidates": [route("C.CO", is_match=True, is_ai=False)]}


class FakeSession:
    def __init__(self):
        self.payload = None

    def post(self, url, json, timeout):
        self.payload = json
        return FakeResponse()


class FakeChemicalScoreResponse:
    status_code = 200

    def json(self):
        return {
            "status": "success",
            "score": 92.5,
            "coverage": 1.0,
            "coverage_details": {"execution_coverage": 1.0},
            "score_tree": {
                "children": [
                    {
                        "id": "feasibility",
                        "score": 90.0,
                        "status": "success",
                        "effective_weight": 0.6,
                    },
                    {
                        "id": "evidence_support",
                        "score": None,
                        "status": "not_applicable",
                        "effective_weight": 0.0,
                    },
                    {
                        "id": "safety",
                        "score": 100.0,
                        "status": "success",
                        "effective_weight": 0.2,
                    },
                    {
                        "id": "economy",
                        "score": 90.0,
                        "status": "success",
                        "effective_weight": 0.2,
                    },
                ]
            },
            "flags": [],
            "engine_version": "0.4.0",
        }


class FakeChemicalScoreSession:
    def __init__(self):
        self.calls = []

    def post(self, url, json, timeout):
        self.calls.append((url, json, timeout))
        return FakeChemicalScoreResponse()


def route(materials: str, *, is_match: bool, is_ai: bool, target: str = "CCO"):
    return {
        "reaction_smiles": f"{materials}>>{target}",
        "target_smiles": target,
        "materials_smiles": materials,
        "source_name": "test",
        "is_match": is_match,
        "is_ai": is_ai,
        "final_score": 0.5,
    }


def test_candidates_require_exact_target_match_and_are_scored():
    client = LocalRetroAPIClient(
        "http://unused.invalid",
        reaction_scorer=StubReactionScorer(),
    )
    candidates = client._filter_routes(
        [
            route("C.CO", is_match=False, is_ai=False),
            route("C.CO", is_match=True, is_ai=False, target="CCN"),
            route("CC.O", is_match=True, is_ai=False),
            route("C.CC", is_match=True, is_ai=True),
            route("C.CO", is_match=True, is_ai=False),
            route("CO.C", is_match=True, is_ai=True),
        ],
        canonicalize_smiles("CCO"),
    )

    assert [candidate["materials_smiles"] for candidate in candidates] == [
        "C.CO",
        "CC.O",
        "C.CC",
    ]
    assert [candidate["chemical_score"] for candidate in candidates] == [
        85.0,
        50.0,
        99.0,
    ]
    assert all(candidate["is_match"] is True for candidate in candidates)
    assert all(candidate["target_smiles"] == "CCO" for candidate in candidates)
    assert candidates[-1]["is_ai"] is True


def test_candidate_envelope_and_overfetch_are_supported():
    client = LocalRetroAPIClient(
        "http://unused.invalid",
        reaction_scorer=StubReactionScorer(),
    )
    client.session = FakeSession()

    candidates = client.get_candidates("CCO", top_k=2)

    assert len(candidates) == 1
    assert client.session.payload["top_k"] == 8


def test_priority_is_non_ai_then_chemical_score_then_legacy_score():
    candidates = [
        {"is_ai": True, "chemical_score": 100, "final_score": 1.0},
        {"is_ai": False, "chemical_score": 60, "final_score": 0.2},
        {"is_ai": False, "chemical_score": 90, "final_score": 0.1},
    ]

    ordered = sorted(candidates, key=route_priority_key)

    assert [candidate["chemical_score"] for candidate in ordered] == [90, 60, 100]


def test_chemical_score_adapter_posts_reaction_smiles_and_caches_result():
    session = FakeChemicalScoreSession()
    scorer = ChemicalReactionScorer(
        api_url="http://chemical-score.test/v1/evaluations",
        timeout=7.5,
        session=session,
    )

    result = scorer.score("CC(=O)O.CCO>>CCOC(C)=O")
    cached = scorer.score("CC(=O)O.CCO>>CCOC(C)=O")

    assert 0 <= result["chemical_score"] <= 100
    assert result["chemical_score_status"] == "success"
    assert set(result["chemical_score_dimensions"]) == {
        "feasibility",
        "evidence_support",
        "safety",
        "economy",
    }
    assert cached == result
    assert session.calls == [
        (
            "http://chemical-score.test/v1/evaluations",
            {"reaction_smiles": "CC(=O)O.CCO>>CCOC(C)=O"},
            7.5,
        )
    ]


def test_formatter_exposes_chemical_ranking_fields():
    selected = route("C.CO", is_match=True, is_ai=False)
    selected.update(StubReactionScorer().score("C.CO>>CCO"))
    tree = {
        "smiles": "CCO",
        "type": "intermediate",
        "is_resolved": True,
        "selected_route": selected,
        "children": [
            {
                "smiles": "C",
                "type": "material",
                "is_resolved": True,
                "children": [],
            },
            {
                "smiles": "CO",
                "type": "material",
                "is_resolved": True,
                "children": [],
            },
        ],
    }

    payload = format_to_test_json([tree])
    reaction = payload["data"]["list"][0]["structures"][0]["children"][0]["reactions"][
        0
    ]

    assert reaction["is_match"] is True
    assert reaction["chemical_score"] == 85.0
    assert reaction["score"] == 85.0
    assert reaction["model_score"] == 0.5


class FakePriceClient:
    def warmup_many(self, smiles, workers=1):
        return None


class FakeStockService:
    def __init__(self, stocked):
        self.stocked = set(stocked)
        self.price_client = FakePriceClient()

    def is_material(self, smiles):
        return smiles in self.stocked

    def get_stock_info(self, smiles):
        return {
            "in_stock": self.is_material(smiles),
            "price": 1.0 if self.is_material(smiles) else -1.0,
            "unit": "CNY",
            "price_unit": "CNY",
            "spec": "",
            "cas": "",
        }


class FakeCandidateClient:
    def __init__(self, root_candidates):
        self.root_candidates = root_candidates
        self.calls = []

    def get_candidates(self, smiles, top_k=10):
        self.calls.append(smiles)
        return list(self.root_candidates) if smiles == "CCO" else []


def ranked_route(materials, *, is_ai, chemical_score):
    return {
        "is_match": True,
        "is_ai": is_ai,
        "chemical_score": chemical_score,
        "final_score": 0.5,
        "materials_smiles": materials,
        "target_smiles": "CCO",
        "_clean_reactants": [materials],
    }


def test_planner_does_not_expand_ai_when_non_ai_route_resolves():
    client = FakeCandidateClient(
        [
            ranked_route("CC", is_ai=True, chemical_score=99),
            ranked_route("C", is_ai=False, chemical_score=50),
        ]
    )
    planner = SynthesisPlanner(
        client,
        FakeStockService({"C", "CC"}),
        top_paths=1,
        top_k_root=2,
    )

    tree = planner.plan("CCO")[0]

    assert tree["is_resolved"] is True
    assert tree["selected_route"]["is_ai"] is False
    assert tree["selected_route"]["materials_smiles"] == "C"


def test_planner_falls_back_to_ai_after_non_ai_route_is_unresolved():
    client = FakeCandidateClient(
        [
            ranked_route("CC", is_ai=True, chemical_score=99),
            ranked_route("CCC", is_ai=False, chemical_score=50),
        ]
    )
    planner = SynthesisPlanner(
        client,
        FakeStockService({"CC"}),
        max_depth=2,
        top_paths=1,
        top_k_root=2,
    )

    tree = planner.plan("CCO")[0]

    assert tree["is_resolved"] is True
    assert tree["selected_route"]["is_ai"] is True
    assert "CCC" in client.calls
