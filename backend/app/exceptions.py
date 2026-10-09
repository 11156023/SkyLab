"""Application-level exceptions for the service layer."""


class AppError(Exception):
    """Base application error."""

    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(AppError):
    def __init__(self, message: str = "Not found"):
        super().__init__(message, 404)


class AuthenticationError(AppError):
    """Raised when the caller's credentials are missing, invalid, or expired.

    Corresponds to HTTP 401. Triggers the frontend's refresh-token flow.
    Use this for token/session problems, NOT for permission checks.
    """

    def __init__(self, message: str = "Could not validate credentials"):
        super().__init__(message, 401)


class PermissionDeniedError(AppError):
    """Raised when the caller is authenticated but lacks permission.

    Corresponds to HTTP 403. Does NOT trigger refresh/logout on the frontend.
    Use this only for role/ownership checks on an already-authenticated user.
    """

    def __init__(self, message: str = "Permission denied"):
        super().__init__(message, 403)


class ConflictError(AppError):
    def __init__(self, message: str = "Conflict"):
        super().__init__(message, 409)


class BadRequestError(AppError):
    def __init__(self, message: str = "Bad request"):
        super().__init__(message, 400)


class UsernamePolicyError(AppError):
    """VM 登入帳號違反命名政策（app/domain/username_policy）；保留違規代碼供呼叫端判讀。"""

    def __init__(self, message: str, codes: list[str]):
        self.codes = codes
        super().__init__(message, 422)


class ProvisioningError(AppError):
    def __init__(self, message: str = "Provisioning failed"):
        super().__init__(message, 500)


class ProxmoxError(AppError):
    def __init__(self, message: str = "Proxmox operation failed"):
        super().__init__(message, 502)


class UpstreamServiceError(AppError):
    def __init__(self, message: str = "Upstream service failed"):
        super().__init__(message, 502)


class GatewayTimeoutError(AppError):
    def __init__(self, message: str = "Upstream service timed out"):
        super().__init__(message, 504)
