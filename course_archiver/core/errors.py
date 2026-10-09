class ArchiverError(Exception):
    """Expected, user-presentable failure."""


class FfmpegMissingError(ArchiverError):
    pass


class StreamDetectionError(ArchiverError):
    pass


class ProtectedStreamError(ArchiverError):
    """Playlist declares encryption/DRM; this tool does not handle it."""


class AccessDeniedError(ArchiverError):
    """CDN answered 401/403/404/410: link expired or not available to this session."""


class FfmpegFailedError(ArchiverError):
    pass


class CancelledError(ArchiverError):
    pass


class ValidationFailedError(ArchiverError):
    pass
