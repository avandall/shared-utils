# 📦 WMS Shared Utilities (shared-utils)

WMS Shared Utilities is a lightweight, internal library containing shared decorators, common models, validation rules, authentication abstractions, and error handlers used across the microservices of the Warehouse Management System (WMS) Ecosystem.

---

## 🛠️ Technology Stack

- **Core Runtime**: Python 3.11+
- **Data Validation**: Pydantic v2 (for shared models and contracts)
- **Security & Auth**: PyJWT (for parsing and validating JSON Web Tokens)
- **Logging**: Python Standard Logging with customizable filters

---

## 🧭 Modules & Library Structure

The library contains the following modules:

*   `shared_utils/auth/` — JWT token helpers (encoding, decoding, verification) used to propagate identity contexts between the `api-gateway` and downstream microservices.
*   `shared_utils/models/` — Shared Pydantic data schemas, base models, and contracts, ensuring API format uniformity.
*   `shared_utils/logging/` — Structured logging configuration helpers, standardizing log output patterns across distinct runtimes.
*   `shared_utils/exceptions/` — Common system error and exception definitions (e.g. ValidationError, UnauthorizedException) to unify HTTP/gRPC error mapping.

---

## 🚀 Setup & Installation

The library is configured to be installed via local directories or standard pip requirements inside your microservice:

### Using `pip` or virtual environments
Include it in your service's `requirements.txt`:
```text
-e ./Libraries/shared-utils
```

Or install it directly using `uv` (recommended):
```bash
uv pip install -e ../../Libraries/shared-utils
```

---

## 💡 Usage Examples

### 1. Structured Logging Setup
Set up standardized structured logs inside a service startup entrypoint:

```python
from shared_utils.logging.logger import setup_logger

# Initialize a globally shared logger instance
logger = setup_logger("inventory-service", level="INFO")

logger.info("Inventory service successfully initialized.")
```

### 2. Validating JWT Auth Tokens
Parse and validate incoming HTTP authorization headers in middleware or routes:

```python
from shared_utils.auth.jwt import decode_jwt_token
from shared_utils.exceptions.common import UnauthorizedException

try:
    token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
    user_context = decode_jwt_token(token, secret_key="JWT_SIGNING_SECRET")
    print(f"Authenticated user: {user_context['user_id']}")
except Exception:
    raise UnauthorizedException("Invalid authentication credentials.")
```
