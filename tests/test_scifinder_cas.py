import copy

from retro_service.scifinder import SciFinderRouteFormatter


class StubPriceClient:
    def __init__(self, cas):
        self.cas = cas

    def get_info(self, smiles):
        return {
            "cas": self.cas,
            "price": -1.0,
            "in_stock": False,
            "unit": "",
            "price_unit": "",
            "spec": "",
            "search_time_sec": 0.0,
        }


def make_raw_structure(*, cas_number=None):
    node = {
        "type": 0,
        "smiles": "SCc1ccccc1",
        "children": [],
    }
    if cas_number is not None:
        node["cas_number"] = cas_number
    return {
        "cas": "route-cas",
        "ok": True,
        "status": "complete",
        "retro_route": {"structures": [node]},
    }


def test_scifinder_cas_number_cannot_be_overwritten_by_price_lookup():
    raw = make_raw_structure(cas_number="100-53-8")
    original = copy.deepcopy(raw)

    converted = SciFinderRouteFormatter(
        StubPriceClient("999-99-9")
    ).convert(raw)

    structure = converted["list"][0]["structures"][0]
    assert structure["cas"] == "100-53-8"
    assert raw == original


def test_price_lookup_cas_is_only_used_when_scifinder_cas_is_missing():
    raw = make_raw_structure()

    converted = SciFinderRouteFormatter(
        StubPriceClient("999-99-9")
    ).convert(raw)

    structure = converted["list"][0]["structures"][0]
    assert structure["cas"] == "999-99-9"
