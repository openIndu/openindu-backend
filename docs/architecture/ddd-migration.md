# Backend domain boundaries

The backend is a modular monolith. Organize code by business capability, then by
domain, application, infrastructure, and interface. Moving files without moving
decisions out of HTTP handlers does not complete a migration.

| Context | Owns | Current entry points | Migration status |
| --- | --- | --- | --- |
| Identity | account registration, SMS authentication, session lifecycle | `api/auth.py`, `services/auth_service.py` | SMS login, registration and sign-in migrated |
| Membership | applications, review, role transition, account restriction and notification | `api/member_applications.py`, `api/users.py` | commands migrated; user-list query remains legacy |
| Catalog | software, documents, tags, publication and downloads | `api/software.py`, `api/documents.py`, `api/tags.py` | legacy |
| Analytics | visits and statistics | `api/visits.py`, `api/stats.py`, `services/geo_service.py` | legacy |
| Knowledge | chat, retrieval and MCP tools | `api/chat.py`, `api/chat_sessions.py`, `mcp/` | legacy |

The migrated identity flow is in `app/identity/`:

- `domain/` decides whether an account may register or authenticate and assigns
  the initial role. It imports no framework, database model or delivery code.
- `application/` coordinates the SMS authentication use case through a code
  verifier and account repository port. It owns the outcome, not HTTP messages.
- `infrastructure/` implements the repository against SQLAlchemy.
- `api/auth.py` and `services/auth_service.py` are compatibility adapters for
  the existing routes and callers. The latter still owns CAPTCHA, SMS delivery,
  JWT lifecycle and response adaptation until those use cases are migrated.

New domain behavior belongs in the owning context, not in `api/` or a global
`services/` directory. Dependencies point inward: API and infrastructure may
depend on application/domain; application depends on domain and ports; domain
depends only on the shared auth error codes and the standard library. The
identity boundary test enforces the latter two layers.

Migration order: finish Identity's CAPTCHA, SMS and session use cases; move
Membership user-list query; then Catalog, Analytics and Knowledge. Keep existing routes and database schema stable during
each move, and add behavior tests for the use case before replacing its route
implementation. The compatibility adapters are removed only after every caller
has moved.
