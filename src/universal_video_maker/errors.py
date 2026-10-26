class ApplicationError(Exception):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class ForbiddenError(ApplicationError):
    def __init__(self, message: str = "The requested action is not permitted.") -> None:
        super().__init__(code="forbidden", message=message, status_code=403)


class NotFoundError(ApplicationError):
    def __init__(self, message: str = "The requested resource was not found.") -> None:
        super().__init__(code="not_found", message=message, status_code=404)


class ConflictError(ApplicationError):
    def __init__(self, message: str = "The requested change conflicts with existing data.") -> None:
        super().__init__(code="conflict", message=message, status_code=409)
