# Clinic Assistant v2: System Architecture

## Architecture Diagram

```mermaid
flowchart TD
    subgraph ClientLayer [Customer & External Auth]
        User[Customer / Patient]
        GCP[Google OAuth 2.0 Provider]
    end

    subgraph HostSystem [Docker Compose Host - Network: clinic-net]
        subgraph GatewayService [FastAPI Gateway :8000]
            AuthRoutes["/auth/login\n/auth/callback\n(Issues JWT: sub, email, scopes, exp)"]
            ChatRoute["/chat\n(Bearer Token Auth)"]
            Agent["LangGraph Orchestrator\n(Forwards Bearer <jwt> to MCPs)"]
            ChatRoute --> Agent
        end

        subgraph MCPServers [FastMCP Domain Services]
            ApptMCP["Appointments MCP :8001\nbook_appointment\nlist_my_appointments\ncancel_appointment"]
            PharmMCP["Pharmacy Inventory MCP :8002\nview_inventory\nadd_inventory"]
            RecMCP["Medical Records MCP :8003\nget_my_medical_records\nget_my_last_medical_record"]
            KnowMCP["Knowledge Retrieval MCP :8004\nsearch_clinic_docs"]
        end

        subgraph VectorStore [Vector Database]
            QdrantDB[("Qdrant :6333\nCollection: clinic_docs")]
        end

        subgraph IngestionService [PDF Ingestion]
            IngestScript["ingest.py\n(Chunks & Embeds Clinic PDFs)"]
        end
    end

    User -- "1. Google Sign-in" --> GCP
    GCP -- "2. Auth Code Redirect" --> AuthRoutes
    AuthRoutes -- "3. Returns Signed JWT" --> User
    User -- "4. POST /chat with Bearer <jwt>" --> ChatRoute

    Agent -- "Tool Calls (Bearer <jwt>)" --> ApptMCP
    Agent -- "Tool Calls (Bearer <jwt>)" --> PharmMCP
    Agent -- "Tool Calls (Bearer <jwt>)" --> RecMCP
    Agent -- "Tool Calls (Bearer <jwt>)" --> KnowMCP

    KnowMCP -- "Semantic Search" --> QdrantDB
    IngestScript -- "Idempotent Upsert" --> QdrantDB
```

## Trust Model and Security Boundaries

All services run on a single host orchestrated via Docker Compose within the isolated `clinic-net` bridge network. 

1. **Authentication & Token Minting:** The FastAPI gateway (Port 8000) acts as the entry point and OAuth 2.0 client. When a customer authenticates with Google, the gateway exchanges the authorization code for verified user information (`sub` and `email`), and mints a cryptographically signed JSON Web Token (JWT) using the `HS256` algorithm and a shared `JWT_SECRET`. The token contains `sub`, `email`, `scopes`, and an expiration time (`exp`, 1 hour).
2. **Shared Secret Verification:** Because FastAPI and the FastMCP domain containers share the same `JWT_SECRET` via environment variables injected by Docker Compose, each FastMCP server independently verifies the signature, expiration, and required scopes of the incoming `Authorization: Bearer <jwt>` header on every tool call. 
3. **Data Isolation:** Domain tools that access patient data (Appointments and Medical Records) take **no user ID parameter**. The identity is derived strictly from the verified `sub` claim in the token. FastMCP servers trust the `sub` claim because only the gateway possesses the `JWT_SECRET` required to sign the token.
4. **Secret Isolation:** Tool secrets and vector database connections remain strictly within their respective container boundaries. The LangGraph agent holds zero domain secrets and acts purely as an orchestrator communicating with tools via MCP over HTTP.
