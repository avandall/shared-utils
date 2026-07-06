from __future__ import annotations


class ServiceError(Exception):
    pass


class NotFoundError(ServiceError):
    pass


class UnauthorizedError(ServiceError):
    pass


class ValidationError(ServiceError):
    pass

