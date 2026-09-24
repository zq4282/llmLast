"""FastAPI 应用入口。"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.chat import router as chat_router
from app.engine.graph import get_graph
from app.session.store import session_store


BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 启动时完成 Redis 连通性和 YAML 校验，避免首个请求才暴露配置错误。
    session_store.ping()
    get_graph()
    yield


app = FastAPI(
    title="Plugin Chat Engine",
    description="基于 LangGraph 和 YAML 业务插件的统一对话接口",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(chat_router, prefix="/api")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/tokens.css", include_in_schema=False)
def design_tokens() -> FileResponse:
    return FileResponse(BASE_DIR / "tokens.css", media_type="text/css")
