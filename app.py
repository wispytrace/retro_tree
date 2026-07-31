from retro_service.api import app
from retro_service.schemas import PlanRequest
from retro_service.orchestration import build_planner
from retro_service.formatter import format_to_test_json


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=9998, reload=True)
