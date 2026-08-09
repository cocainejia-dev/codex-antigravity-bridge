# Shared Contract

The example tasks agree on this interface before implementation starts:

- `GET /api/items` returns HTTP 200;
- the response is a JSON array;
- every item contains `id` and `name`;
- the frontend reads the endpoint without changing the backend-owned files;
- the test task verifies the response shape.

Suggested ownership:

| Task | Owned paths | Verification |
| --- | --- | --- |
| backend | `backend/` | `python -m pytest backend` |
| frontend | `frontend/` | `python -m pytest frontend` |
| tests | `tests/` | `python -m pytest tests` |
