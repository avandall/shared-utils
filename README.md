# shared-utils

Shared utilities dùng chung giữa các services (scaffold ban đầu).

Mục tiêu (Phase 0):
- DTOs/contracts dùng chung (nếu cần)
- Logging helpers
- Config helpers
- Auth helper (ví dụ: token parsing) dùng lại giữa gateway và services

## Modules
- `shared_utils/auth/`: JWT helpers (encode/decode/validate)
- `shared_utils/models/`: shared DTOs/contracts (Pydantic)
- `shared_utils/logging/`: logging setup helpers
- `shared_utils/exceptions/`: common error definitions
