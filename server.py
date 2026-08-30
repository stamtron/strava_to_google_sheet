"""
Web Dashboard Server Entry Point.
"""

import uvicorn

from src.config import SERVER_HOST, SERVER_PORT

if __name__ == "__main__":
    print(f"🌐 Dashboard: http://{SERVER_HOST}:{SERVER_PORT}")
    uvicorn.run("src.api.server:app", host=SERVER_HOST, port=SERVER_PORT, reload=True)
