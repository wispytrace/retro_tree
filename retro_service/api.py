import logging

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse

from .chemistry import RDKIT_AVAILABLE
from .config import APP_DESCRIPTION, APP_TITLE, APP_VERSION, DEFAULT_SCIFINDER_TIMEOUT
from .orchestration import build_planner, plan_with_local_and_scifinder
from .schemas import PlanRequest
from .visualizer import RouteVisualizer


app = FastAPI(title=APP_TITLE, description=APP_DESCRIPTION, version=APP_VERSION)


@app.post("/api/plan/download", summary="规划路径并直接下载图片")
def plan_and_download(request: PlanRequest):
    try:
        planner = build_planner(request)
        result_trees = planner.plan(request.smiles)
        img_bytes = RouteVisualizer.generate_image_bytes(result_trees)

        if not img_bytes:
            raise HTTPException(status_code=500, detail="未安装 RDKit/Graphviz，或者图片生成失败。")

        safe_smiles = "".join([c for c in request.smiles if c.isalnum() or c in "-_="])[:15]
        filename = f"synthesis_{safe_smiles}.png"
        return Response(
            content=img_bytes,
            media_type="image/png",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logging.exception("Planning download error")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/plan", summary="获取规划路径的树状数据(目标JSON格式)")
def plan_synthesis_json(request: PlanRequest):
    formatted_data = plan_with_local_and_scifinder(request, timeout_sec=DEFAULT_SCIFINDER_TIMEOUT)
    return JSONResponse(content=formatted_data)


@app.get("/health", summary="健康检查")
def health():
    return {"status": "ok", "rdkit_available": RDKIT_AVAILABLE}
