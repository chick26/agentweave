from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from agent_runtime.common import load_env_file
from agent_runtime.server.app import create_app


def main() -> None:
    root = Path.cwd().resolve()
    env_path = root / ".env"
    if env_path.exists():
        load_env_file(env_path)
    host = os.getenv("AGENTWEAVE_SERVER_HOST", "127.0.0.1")
    port = int(os.getenv("AGENTWEAVE_SERVER_PORT", "8765"))
    token = os.getenv("AGENTWEAVE_SERVER_TOKEN", "")
    app = create_app(host=host, token=token, root=root)
    print(f"AgentWeave server listening on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
