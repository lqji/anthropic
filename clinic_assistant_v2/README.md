# Clinic Assistant v2: Dockerised FastMCP with Qdrant RAG & Google OAuth

Clinic Assistant v2 refactors the front-desk conversational AI into modular, containerised FastMCP domain servers with Google OAuth 2.0 authentication and signed JWT verification. Every customer only ever accesses their own records.

## Architecture

See [docs/architecture.md](docs/architecture.md) for the complete system topology and trust boundary model.

Services:
- **FastAPI Gateway** (`:8000`): Authentication (`/auth/login`, `/auth/callback`), JWT minting, and conversational `/chat` orchestration via LangGraph.
- **Appointments MCP** (`:8001`): `book_appointment`, `list_my_appointments`, `cancel_appointment`.
- **Pharmacy Inventory MCP** (`:8002`): `view_inventory`, `add_inventory`.
- **Medical Records MCP** (`:8003`): `get_my_medical_records`, `get_my_last_medical_record`.
- **Knowledge Retrieval MCP** (`:8004`): `search_clinic_docs` over Qdrant.
- **Qdrant Vector Database** (`:6333`): Collection `clinic_docs`.

---

## 1. Setup Google OAuth 2.0 Client in GCP

1. Go to Google Cloud Console: **APIs & Services** → **Credentials**.
2. Click **Create Credentials** → **OAuth client ID**.
3. Select Application type: **Web application**.
4. Set Authorized redirect URIs to:
   ```text
   http://localhost:8000/auth/callback
   ```
5. Copy your **Client ID** and **Client Secret**.

---

## 2. Configure Environment

Copy `.env.example` to `.env` in `clinic_assistant_v2/`:

```bash
cp .env.example .env
```

Edit `.env`:
```ini
OPENAI_API_KEY=your-openai-api-key
GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-google-client-secret
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/callback
JWT_SECRET=generate-a-secure-random-32-char-string
QDRANT_URL=http://qdrant:6333
```

*(Note: Never commit `.env`. It is gitignored.)*

---

## 3. Run with Docker Compose

Start all services and Qdrant in Docker:

```bash
docker compose up --build -d
```

Verify containers are running:
```bash
docker compose ps
```

Health check:
```bash
curl http://localhost:8000/health
```

---

## 4. Ingest Clinic Documentation into Qdrant

Run the idempotent PDF ingestion workflow:

```bash
uv run python ingestion/ingest.py
```

Output:
```text
doctors.pdf: 3 chunks stored
facilities.pdf: 3 chunks stored
policies.pdf: 3 chunks stored
```

---

## 5. Log in & Chat

1. Open your browser and navigate to:
   ```text
   http://localhost:8000/auth/login
   ```
2. Complete Google sign-in. Google will redirect back to `/auth/callback`, which returns your signed JWT:
   ```json
   {
     "access_token": "eyJhbGciOi...",
     "token_type": "bearer",
     "sub": "1029384756...",
     "email": "user@gmail.com"
   }
   ```

3. Call the `/chat` endpoint with your Bearer token:
   ```bash
   curl -X POST http://localhost:8000/chat \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer <access_token>" \
     -d '{"message": "Show me my last medical record"}'
   ```

---

## 6. Run Tests

Run the test suite (cross-user isolation, ID prompting guard, token rejection, tools, and auth):

```bash
PYTHONPATH=. uv run --python 3.14 pytest -v
```
