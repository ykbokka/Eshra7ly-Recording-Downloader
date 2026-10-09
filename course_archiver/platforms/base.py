from abc import ABC, abstractmethod


class baseplatform(ABC):
    @abstractmethod
    def extract_media_info(self, url: str) -> dict:
        """Return {"type": "hls", "captures": [core.models.Capture], "headers": {...}, "title": str}.

        ``captures`` are the HLS playlists the authorized browser session itself received.
        """
