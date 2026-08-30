"""
Web Dashboard Server Entry Point.
"""

import uvicorn
from src.api.server import app

if __name__ == "__main__":
    uvicorn.run("src.api.server:app", host="0.0.0.0", port=8000, reload=True)
