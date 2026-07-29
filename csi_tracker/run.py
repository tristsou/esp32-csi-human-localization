import os

import uvicorn

if __name__ == "__main__":
    host = os.environ.get("CSI_TRACKER_HOST", "0.0.0.0")
    port = int(os.environ.get("CSI_TRACKER_PORT", "8000"))
    uvicorn.run("backend.app:app", host=host, port=port, reload=False)
