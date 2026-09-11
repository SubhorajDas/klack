"""Expected messaging failures translated by the HTTP adapter."""


class MessagingError(Exception):
    """Base class for an expected messaging operation failure."""


class InvalidMessageBody(MessagingError, ValueError):
    """The supplied message body violates the supported bounds."""


class MessageNotFound(MessagingError):
    """The requested message is absent from the addressed channel."""


class MessagePermissionDenied(MessagingError):
    """The current identity does not own the requested message."""


class MessageDeleted(MessagingError):
    """A deleted message cannot be edited."""


class InvalidMessageCursor(MessagingError):
    """The history cursor does not identify a message in this channel."""
