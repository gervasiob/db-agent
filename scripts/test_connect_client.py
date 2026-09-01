from __future__ import annotations

import warnings

warnings.filterwarnings("ignore")

from starlette.testclient import TestClient
import app.main  # noqa: E402

client = TestClient(app.main.app)
payload = dict(
    database_type="postgresql",
    host="127.0.0.1",
    port=5433,
    database_name="lecatex",
    username="postgres",
    password="admin",
    schema="public",
    ssl_mode=None,
)

r = client.post("/api/v1/database/connect", json=payload)
print("HTTP", r.status_code)
print(r.text[:800])
