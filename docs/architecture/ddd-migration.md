# Backend domain boundaries

The backend is a modular monolith. Organize code by business capability, then by
domain, application, infrastructure, and interface. Moving files without moving
decisions out of HTTP handlers does not complete a migration.

## Layout

```text
app/
  web_app.py  mcp_app.py   entry points (uvicorn app.web_app:app, app.mcp_app:app)
  api/                     HTTP route adapters
  mcp/                     MCP tool adapters
  modules/                 one package per business context
    identity/  membership/  catalog/  analytics/  knowledge/
  core/  domain/  models/  services/  middleware/  tasks/
                           code shared by the contexts
```

The contexts never import each other; they depend only on the shared layers, and
`tests/unit/test_identity_boundaries.py` checks both that and the inward direction below.

| Context | Owns | Current entry points | Migration status |
| --- | --- | --- | --- |
| Identity | account registration, SMS authentication, session lifecycle | `api/auth.py`, `services/auth_service.py` | migrated: SMS auth, CAPTCHA, verification codes, token/session lifecycle; `services/auth_service.py` removed |
| Membership | applications, review, role transition and notification | `api/member_applications.py`, `api/users.py` | migrated: apply/review rules, approval SMS notification, account administration (role change, blacklist, force logout, soft delete); both API files are thin adapters |
| Catalog | software, documents, tags, publication and downloads | `api/software.py`, `api/documents.py`, `api/tags.py` | done: software submissions, direct-to-OSS pipeline, downloads, publishing, documents and tags migrated (`app/modules/catalog/`) |
| Analytics | visits and statistics | `api/visits.py`, `api/stats.py`, `services/geo_service.py` | migrated: visit tracking, IP geolocation (`services/geo_service.py` removed), reporting windows and dashboard SQL read models moved into `app/modules/analytics/`; both API files are thin adapters |
| Knowledge | chat, retrieval and MCP tools | `api/chat.py`, `api/chat_sessions.py`, `mcp/` | migrated: consultation flow (quota, query rewriting, brand-scoped retrieval with retry, SSE answering), session CRUD and feedback, Milvus search and LLM gateway (`services/chat_service.py` and `services/milvus_service.py` removed); API files and `mcp/` are thin adapters |

The migrated contexts follow the same layout (`app/modules/identity/`, `app/modules/membership/`):

- `domain/` holds the business rules. It imports no framework, database model
  or delivery code.
- `application/` coordinates use cases through ports (repositories, gateways,
  audit log, clock). It owns the outcome, not HTTP messages.
- `infrastructure/` implements the ports against SQLAlchemy, PyJWT/PIL and the
  shared SMS client.
- `provider.py` is the composition root wiring adapters into use cases.
- The `api/*.py` files are thin adapters for the existing routes.

New domain behavior belongs in the owning context, not in `api/` or a global
`services/` directory. Dependencies point inward: API and infrastructure may
depend on application/domain; application depends on domain and ports; domain
depends only on the shared error codes and the standard library. The boundary
test (`tests/unit/test_identity_boundaries.py`) enforces the latter two layers
for every migrated context.

Migration order: Identity (done; `services/auth_service.py` removed),
Membership (done; approval SMS and audit stay transactional as before),
Catalog (done: software, then documents and tags), then
Analytics and Knowledge. Keep existing routes and database schema
stable during each move, and add behavior tests for the use case before
replacing its route implementation. The compatibility adapters are removed
only after every caller has moved.
