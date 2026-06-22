"""Busca live ativa da CazeTV via YouTube Data API v3."""

import json
import logging
import os
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_CAZETV_CHANNEL_ID = "UCZiYbVptd3PVPf4f6eR6UaQ"
_YT_SEARCH = "https://www.googleapis.com/youtube/v3/search"


def get_cazetv_live() -> str | None:
    """Retorna a URL da live ativa da CazeTV, ou None se não houver / sem API key."""
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        return None

    params = urllib.parse.urlencode({
        "part": "snippet",
        "channelId": _CAZETV_CHANNEL_ID,
        "type": "video",
        "eventType": "live",
        "maxResults": 1,
        "key": api_key,
    })

    try:
        req = urllib.request.Request(
            f"{_YT_SEARCH}?{params}",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        items = data.get("items", [])
        if not items:
            return None
        video_id = items[0]["id"]["videoId"]
        return f"https://www.youtube.com/watch?v={video_id}"
    except Exception:
        logger.warning("Falha ao buscar live da CazeTV na YouTube API")
        return None
