"""本地启动入口：python run.py。"""

import os
import sys

import uvicorn


def should_reload() -> bool:
    """普通开发时启用热更新；调试器附着时禁用子进程，确保断点命中。"""

    configured = os.getenv("APP_RELOAD", "true").strip().lower() in {"1", "true", "yes", "on"}
    debugger_attached = sys.gettrace() is not None
    return configured and not debugger_attached


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=should_reload())
