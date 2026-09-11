"""Expected channel failures translated by the HTTP adapter."""


class ChannelError(Exception):
    """Base class for an expected channel operation failure."""


class InvalidChannelName(ChannelError, ValueError):
    """The supplied channel name cannot form a supported slug."""


class ChannelNotFound(ChannelError):
    """The channel is absent or intentionally hidden from the actor."""


class ChannelPermissionDenied(ChannelError):
    """The actor's current workspace role cannot perform the operation."""


class ChannelNameConflict(ChannelError):
    """The workspace already contains a channel with this name."""


class ChannelMembershipNotFound(ChannelError):
    """The requested channel membership does not exist."""


class TargetWorkspaceMembershipNotFound(ChannelError):
    """The target identity is not a current member of the workspace."""


class ChannelArchived(ChannelError):
    """The requested mutation is unavailable while the channel is archived."""
