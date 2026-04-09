# Weve Backend (FastAPI + Supabase Auth)

Auth-only API using Supabase (no database/Prisma). Signup sends a verification email; login returns a valid auth token.

## Setup

### 1. Environment

```bash
cd backend
cp .env.example .env
```

Edit `.env`:

- **SUPABASE_URL**: Supabase project URL (Settings → API).
- **SUPABASE_ANON_KEY**: Supabase anon/public key (Settings → API).
- **FRONTEND_URL**: Base URL of the frontend, e.g. `http://localhost:3000`. Used as the redirect after email verification (user is sent to `{FRONTEND_URL}/login?verified=1`).

### 2. Supabase Dashboard

- **Auth → Providers → Email**: Enable “Confirm email” so signup sends a verification email.
- **Auth → URL Configuration → Redirect URLs**: Add your login URL so the verification link can redirect back, e.g. `http://localhost:3000/login` or `http://localhost:3000/login?verified=1`.

### 3. Python

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Run

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

API base: **http://localhost:8000**

- **Health**: `GET /health`
- **Signup**: `POST /api/auth/signup` — sends verification email; response message asks user to verify.
- **Login**: `POST /api/auth/login` — returns `access_token`, `refresh_token`, and `user` (only for verified users).

## API Examples

### Signup (sends verification email)

```bash
curl -X POST http://localhost:8000/api/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"secret123","full_name":"Your Name","username":"you"}'
```

Response: `{"message":"Please check your email and verify your account."}`

### Login (after email verification)

```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"secret123"}'
```

Response includes `access_token`, `refresh_token`, and `user` (id, email, full_name, username). Use the token in the `Authorization: Bearer <token>` header for protected routes.
# weve-backend
# weve-backend
