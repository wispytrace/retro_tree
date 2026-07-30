from pydantic import BaseModel, Field

from .config import DEFAULT_API_TIMEOUT, DEFAULT_PRICE_TIMEOUT, DEFAULT_TIME_BUDGET


class PlanRequest(BaseModel):
    smiles: str
    max_depth: int = Field(default=4, ge=1, le=8)
    top_paths: int = Field(default=3, ge=1, le=20)

    top_k_root: int = Field(default=5, ge=1, le=50)
    top_k_inner: int = Field(default=3, ge=1, le=30)
    max_nodes: int = Field(default=500, ge=20, le=20000)
    time_budget_sec: float = Field(default=DEFAULT_TIME_BUDGET, gt=1, le=600)
    api_timeout_sec: float = Field(default=DEFAULT_API_TIMEOUT, gt=0.5, le=120)
    price_timeout_sec: float = Field(default=DEFAULT_PRICE_TIMEOUT, gt=0.5, le=60)
    parallel_workers: int = Field(default=8, ge=1, le=64)
    max_reactants_per_step: int = Field(default=5, ge=1, le=20)
