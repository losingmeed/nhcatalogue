"""nhentai access layer — the only code that touches the network."""

from .api import NhentaiClient
from .domains import DomainRotator
from .errors import BlockedError, FlareSolverrError, NhentaiError, NotFoundError
from .models import (
    Gallery,
    Page,
    SearchResult,
    Tag,
    normalize_tag_name,
    normalize_type,
    sniff_image_ext,
)
from .ratelimit import RateLimiter
from .transport import HAS_CURL_CFFI, FlareSolverrSession, Transport, TransportResponse

__all__ = [
    "NhentaiClient",
    "DomainRotator",
    "RateLimiter",
    "BlockedError",
    "NotFoundError",
    "NhentaiError",
    "FlareSolverrError",
    "Gallery",
    "SearchResult",
    "Tag",
    "Page",
    "normalize_tag_name",
    "normalize_type",
    "sniff_image_ext",
    "Transport",
    "TransportResponse",
    "FlareSolverrSession",
    "HAS_CURL_CFFI",
]
