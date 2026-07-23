from __future__ import annotations

import multiprocessing
import os

import uvicorn

from server.app import app


def main() -> None:
    multiprocessing.freeze_support()
    port = int(os.environ.get("FLOWLAB_BACKEND_PORT", "8787"))
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        workers=1,
        access_log=True,
    )


if __name__ == "__main__":
    main()
