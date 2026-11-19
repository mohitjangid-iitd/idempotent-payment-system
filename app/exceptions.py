class InsufficientBalance(Exception):
    pass

class BalanceNotZero(Exception):
    pass

class AccountNotFound(Exception):
    pass

class UserAlreadyExists(Exception):
    pass

class InvalidTransfer(Exception):
    pass

class RequestBodyChangeError(Exception):
    pass

class RequestInProgress(Exception):
    pass

class PreviousAttemptFailed(Exception):
    pass