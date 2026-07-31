# Users API

User management module built with FastAPI: registration, JWT authentication,
email/SMS verification, role-based access control, user administration and
automatic removal of accounts that never complete verification.

Fully asynchronous, structured as an extensible modular monolith, and shipped
with Docker, Alembic migrations and a Celery worker.

---

## Table of contents

- [Quick start (Docker)](#quick-start-docker)
- [Quick start (local)](#quick-start-local)
- [API reference](#api-reference)
- [Architecture](#architecture)
- [Domain model](#domain-model)
- [Key design decisions](#key-design-decisions)
- [Background jobs](#background-jobs)
- [Configuration](#configuration)
- [Postman collection](#postman-collection)
- [Testing and linting](#testing-and-linting)
- [Deliberate simplifications](#deliberate-simplifications)

---

## Quick start (Docker)

```bash
cp .env.example .env          # adjust POSTGRES_PORT/API_PORT if those are taken
docker compose up --build -d
```

This starts five services: `api`, `worker` (Celery), `beat` (scheduler), `db`
(PostgreSQL 16) and `redis`. Migrations run automatically from the API
container's entrypoint before the server starts.

| URL | What |
|---|---|
| http://localhost:8000/docs | Swagger UI |
| http://localhost:8000/redoc | ReDoc |
| http://localhost:8000/openapi.json | OpenAPI schema |
| http://localhost:8000/health | Liveness probe |

A verified administrator is created on first start from `FIRST_ADMIN_EMAIL` /
`FIRST_ADMIN_PASSWORD` (`admin@example.com` / `Admin12345` in the example env),
because otherwise there is no way to reach the admin-only endpoints on a fresh
database.

Verification codes are not emailed in development — they are printed to the API
log:

```bash
docker compose logs -f api
```

```
==================== VERIFICATION CODE ====================
  To:      ada@example.com
  Code:    261621
  Link:    http://localhost:8000/verify?email=ada%40example.com&code=261621
  Expires: in 15 minutes
===========================================================
```

Shut down with `docker compose down` (add `-v` to drop the database volume).

## Quick start (local)

Requires Python 3.12+. PostgreSQL is optional — SQLite works out of the box.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\activate
pip install -r requirements-dev.txt

cp .env.example .env
# For a zero-dependency run, set in .env:
#   DATABASE_URL=sqlite+aiosqlite:///./users_api.db

alembic upgrade head
uvicorn app.main:app --reload
```

Celery needs Redis; with the broker running:

```bash
celery -A app.workers.celery_app.celery_app worker --loglevel=INFO
celery -A app.workers.celery_app.celery_app beat --loglevel=INFO
```

### A complete flow with curl

```bash
# 1. Register — the account starts unverified
curl -X POST localhost:8000/auth/signup -H 'Content-Type: application/json' \
  -d '{"email":"ada@example.com","password":"Str0ngPassw0rd","first_name":"Ada"}'

# 2. Take the code from the API log, then confirm it
curl -X POST localhost:8000/auth/verify -H 'Content-Type: application/json' \
  -d '{"email":"ada@example.com","code":"261621"}'

# 3. Log in
curl -X POST localhost:8000/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"ada@example.com","password":"Str0ngPassw0rd"}'

# 4. Call an authenticated endpoint
curl localhost:8000/me -H "Authorization: Bearer <access_token>"

# 5. Rotate the access token
curl -X POST localhost:8000/auth/refresh -H 'Content-Type: application/json' \
  -d '{"refresh_token":"<refresh_token>"}'
```

---

## API reference

Every endpoint carries an English `summary` and `description` and is documented
in Swagger UI. Errors share one shape:

```json
{ "error": "email_already_registered", "message": "A user with this email already exists", "details": {} }
```

### Authentication

| Method | Path | Access | Description |
|---|---|---|---|
| POST | `/auth/signup` | public | Register; creates an unverified account and sends a code |
| POST | `/auth/login` | public | Exchange credentials for an access + refresh pair |
| POST | `/auth/refresh` | public | Rotate the refresh token, get a new access token |
| POST | `/auth/verify` | public | Confirm a verification code |
| POST | `/auth/verify/resend` | public | Issue a new code, invalidating the previous one |
| POST | `/auth/logout` | public | Revoke one refresh token |
| POST | `/auth/logout/all` | authenticated | Revoke every session of the caller |

### Profile

| Method | Path | Access | Description |
|---|---|---|---|
| GET | `/me` | authenticated | The current user |
| PATCH | `/me` | authenticated | Update own profile fields |
| POST | `/me/password` | authenticated | Change own password |

### Users

| Method | Path | Access | Description |
|---|---|---|---|
| GET | `/users` | admin | Paginated list with search and filters |
| POST | `/users` | admin | Create a user with an explicit role |
| GET | `/users/{id}` | admin | Fetch a user by id |
| PATCH | `/users/{id}` | self or admin | Partial update |
| DELETE | `/users/{id}` | admin | Delete a user and their sessions |

`GET /users` accepts `limit`, `offset`, `search`, `role`, `is_verified` and
`is_active`, and returns `{items, total, limit, offset}`.

`PATCH` is genuinely partial: only keys present in the body are applied.
`email`, `role`, `is_active` and `is_verified` are administrator-only; a regular
user sending them gets `403` rather than a silently ignored field.

### Health

`GET /health` (liveness) and `GET /health/ready` (checks the database).

---

## Architecture

A **modular monolith**: one deployable, split into feature modules that own
their models, schemas, data access and routes. A module can be extracted into a
service later because nothing outside it reaches for its tables directly.

```
app/
├── main.py                 Application factory, exception handlers, health routes
├── bootstrap.py            First-run administrator creation
├── core/                   Cross-cutting concerns, no framework or DB coupling
│   ├── config.py             Typed settings from the environment
│   ├── security.py           Argon2 hashing, one-time codes, JWT encode/decode
│   ├── exceptions.py         Domain errors mapped to HTTP by a single handler
│   ├── pagination.py         Page[T] and the pagination dependency
│   └── logging.py
├── db/
│   ├── base.py               DeclarativeBase, UUID/timestamp mixins, GUID type
│   ├── session.py            Async engine, session factory, FastAPI dependency
│   └── registry.py           Imports every model for Alembic and tests
├── api/
│   ├── deps.py               Sessions, services, get_current_user, role guards
│   ├── responses.py          Shared OpenAPI error documentation
│   └── router.py             Aggregates module routers under API_PREFIX
├── modules/
│   ├── users/                models · schemas · repository · service · router
│   └── auth/                 models · schemas · repository · service · router
├── notifications/          Notifier protocol + console implementation
└── workers/                Celery app and scheduled tasks
```

### Layering

```
router  →  service  →  repository  →  model
   ↑          ↑            ↑
 HTTP     business     SQLAlchemy
 only      rules        queries
```

* **Router** — HTTP only: validation via schemas, status codes, OpenAPI text.
  No business rules.
* **Service** — use cases and authorization rules. Raises domain exceptions
  (`AppError` subclasses), never `HTTPException`, so the same code is callable
  from a Celery task or a CLI.
* **Repository** — the only place that builds queries. Swapping storage or
  adding a cache is a local change.
* **Model** — SQLAlchemy 2.0 typed ORM classes.

Everything is async end to end: `asyncpg`, `AsyncSession`, async routes.

### Extension points

* A new module is a directory under `app/modules/` plus one line in
  `app/api/router.py` and one in `app/db/registry.py`.
* A real email/SMS provider is a new class implementing the `Notifier`
  protocol, returned from `get_notifier()`.
* `API_PREFIX` turns the routes into a versioned API without touching routers.

---

## Domain model

**users** — `id` (UUID), `email` (unique, lower-cased), `hashed_password`,
`first_name`, `last_name`, `phone`, `role` (`user` | `admin`), `is_verified`,
`is_active`, `verified_at`, `last_login_at`, `created_at`, `updated_at`.

**verification_codes** — one-time codes: `user_id`, `channel` (`email` | `sms`),
`destination`, `code_hash`, `expires_at`, `used_at`, `attempts`.

**refresh_tokens** — issued sessions: `user_id`, `jti`, `expires_at`,
`revoked_at`, `replaced_by_jti`.

Both child tables cascade on user deletion (`ON DELETE CASCADE`, with the
SQLite foreign-key pragma enabled so the same holds there).

---

## Key design decisions

**Argon2id for passwords.** Current best practice, and free of bcrypt's silent
72-byte truncation. Stored hashes are re-hashed on login when the cost
parameters change (`password_needs_rehash`).

**Stateful refresh tokens.** Access tokens stay stateless and short-lived
(15 min). Refresh tokens are recorded by `jti` so they can be revoked, and they
**rotate on every use**. Presenting an already-rotated token is treated as theft:
every session of that user is revoked and the attempt is logged.

**Hashed verification codes.** Codes are bearer secrets; only an HMAC-SHA256
digest is stored, so a database dump cannot be used to verify accounts. Codes
are single-use, expire, and lock after `VERIFICATION_MAX_ATTEMPTS` failures.

**No account enumeration.** Login answers identically for an unknown email and a
wrong password — and still hashes a dummy value, so the two paths take
comparable time. `/auth/verify/resend` always returns the same message.

**Unverified users may log in.** They need a session to see their own profile
and to finish verification. Endpoints demanding a confirmed account use the
`get_verified_user` dependency.

**Authorization lives in the service layer.** `update_user` and `delete_user`
enforce "self or admin" themselves, so the rule cannot be bypassed by a new
caller that forgets a router guard. Two lockout guards: an admin can neither
demote nor delete their own account.

**UUID primary keys.** Row identifiers appear in public URLs; sequential
integers would leak user counts and invite enumeration.

**Uniform errors.** Services raise `AppError` subclasses carrying a status code
and a machine-readable `error_code`; one handler renders them. Unexpected
exceptions log a traceback and return a generic 500 — internals never reach the
client.

**Migrations run in the entrypoint, not in the app lifespan.** A rolling
deployment then migrates once instead of once per replica.

---

## Background jobs

Celery with Redis as broker; `beat` provides the schedule.

| Task | Schedule | What it does |
|---|---|---|
| `purge_unverified_users` | every `CLEANUP_INTERVAL_MINUTES` (60) | Deletes users still unverified `UNVERIFIED_USER_TTL_DAYS` (**2**) after registration |
| `purge_expired_refresh_tokens` | daily | Drops refresh tokens that expired over a week ago |

The cleanup task works in bounded batches (`CLEANUP_BATCH_SIZE`) so a large
backlog never holds one long transaction open, and retries with backoff on a
transient database failure. Deleting a user cascades to their codes and
sessions.

Run it by hand:

```bash
docker compose exec worker python -c \
  "from app.workers.tasks import purge_unverified_users; print(purge_unverified_users.delay().get(timeout=60))"
```

Celery workers are synchronous while the data layer is async, so each task runs
its coroutine via `asyncio.run` on a private engine that is disposed afterwards
— asyncpg connections are bound to the loop that created them, so a shared pool
would hand out connections attached to a closed loop.

---

## Configuration

Everything comes from environment variables (or `.env`); see
[.env.example](.env.example) for the full list with defaults.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://...` | Any SQLAlchemy async URL; `sqlite+aiosqlite:///./users_api.db` also works |
| `SECRET_KEY` | insecure placeholder | JWT signing and code HMAC. Startup **fails** if left as-is with `ENVIRONMENT=prod` |
| `ACCESS_TOKEN_TTL_MINUTES` | `15` | Access token lifetime |
| `REFRESH_TOKEN_TTL_DAYS` | `30` | Refresh token lifetime |
| `VERIFICATION_CHANNEL` | `email` | `email` or `sms` |
| `VERIFICATION_CODE_TTL_MINUTES` | `15` | Code lifetime |
| `UNVERIFIED_USER_TTL_DAYS` | `2` | Retention window for unverified accounts |
| `API_PREFIX` | empty | Set to `/api/v1` to serve a versioned API |
| `FIRST_ADMIN_EMAIL` / `_PASSWORD` | — | Bootstrap admin; skipped when blank or when an admin exists |

> `.env` is parsed by python-dotenv: keep comments on their own line, since a
> trailing `# ...` after an empty value becomes part of that value.

---

## Postman collection

[postman/](postman/) holds a ready-to-run collection and a local environment:

| File | Purpose |
|---|---|
| `users-api.postman_collection.json` | 23 requests across Health, Authentication, Profile, Users (admin) and Access control checks |
| `users-api.postman_environment.json` | `baseUrl` and the bootstrap administrator credentials |

Import both into Postman, pick the **Users API — local** environment and run the
requests top to bottom. Tokens and identifiers are captured into collection
variables by test scripts, so nothing has to be copied by hand: signing up
generates a unique address, logging in stores the token pair, and the admin
folder authenticates with its own token.

Every request carries assertions, so `Run collection` doubles as a smoke test of
a deployment — it also runs headless:

```bash
npx newman run postman/users-api.postman_collection.json \
  -e postman/users-api.postman_environment.json
```

One step is manual by design: verification codes are written to the log rather
than emailed, so before **Verify account** read the code from
`docker compose logs --tail 50 api` and paste it into the `verificationCode`
variable. Supplying it on the command line works too:

```bash
npx newman run postman/users-api.postman_collection.json \
  -e postman/users-api.postman_environment.json \
  --env-var userEmail=<address> --env-var verificationCode=<code>
```

The **Access control checks** folder is deliberately made of negative cases —
403 for a regular user on an admin route, 401 without a token, a blocked
self-escalation, a duplicate registration and a wrong password — since those
boundaries are the point of the module.

## Testing and linting

```bash
pytest                 # 39 tests, SQLite in-memory, no external services
ruff check .
alembic check          # verifies migrations match the models
```

Covered: the full signup → verify → login → refresh cycle, single-use and
expiring codes, refresh rotation and replay detection, account-enumeration
resistance, every role restriction on every endpoint, pagination and filtering,
password change, retention selection, and cascade deletes.

---

## Deliberate simplifications

Marked in code with a `SIMPLIFICATION` comment describing the production
approach.

1. **Console notifier** ([app/notifications/console.py](app/notifications/console.py)) —
   codes are logged instead of sent, which the task permits for development.
   In production: an SMTP/SendGrid and a Twilio implementation of the same
   `Notifier` protocol, delivery dispatched to Celery so a slow provider never
   blocks signup, retries with backoff, templated messages, and a suppression
   list fed by bounce webhooks.
2. **No rate limiting** ([app/modules/auth/service.py](app/modules/auth/service.py)) —
   `/auth/login` and `/auth/verify/resend` are the obvious targets. In
   production: a Redis token bucket per IP and per address, plus temporary
   account lockout after repeated failures.
3. **Access tokens cannot be revoked before expiry**
   ([app/modules/auth/service.py](app/modules/auth/service.py)) — logout revokes
   the refresh token only. Immediate revocation needs a Redis deny-list of
   `jti`s checked on every request; at a 15-minute TTL the trade-off is
   deliberate.
4. **Password strength is length plus a letter/digit mix**
   ([app/modules/users/schemas.py](app/modules/users/schemas.py)) — production
   would add a breached-password check (HIBP k-anonymity) and a
   common-password dictionary.
5. **Administrative email change keeps the verification status**
   ([app/modules/users/service.py](app/modules/users/service.py)) — the correct
   flow stores a pending address, sends a code to it, and swaps only once
   confirmed, so an account is never left in a state the retention job would
   delete.
6. **No audit log** — administrative actions are logged, not stored. A
   regulated deployment would persist an append-only audit table.
