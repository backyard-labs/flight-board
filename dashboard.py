from pathlib import Path
import threading
from http.server import ThreadingHTTPServer

import app

# Reuse the existing authenticated feed and API with a new interface.
app.PAGE = Path("/app/dashboard.html").read_text(encoding="utf-8-sig")

if __name__ == "__main__":
    threading.Thread(target=app.fetch_loop, daemon=True).start()
    print("Four-aircraft dashboard listening on port 8000", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8000), app.Handler).serve_forever()