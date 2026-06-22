"""Busca live ativa ou agendada da CazeTV via YouTube Data API v3."""

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_CAZETV_HANDLE = "CazeTV"
_YT_CHANNELS = "https://www.googleapis.com/youtube/v3/channels"
_YT_PLAYLIST = "https://www.googleapis.com/youtube/v3/playlistItems"
_YT_VIDEOS = "https://www.googleapis.com/youtube/v3/videos"
_BRT = timezone(timedelta(hours=-3))

_resolved_channel_id: str | None = None
_uploads_playlist_id: str | None = None


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.loads(r.read())
    return data


def _resolve_channel(api_key: str) -> tuple[str, str] | tuple[None, None]:
    """Retorna (channel_id, uploads_playlist_id) resolvendo via @CazeTV handle."""
    global _resolved_channel_id, _uploads_playlist_id
    if _resolved_channel_id and _uploads_playlist_id:
        return _resolved_channel_id, _uploads_playlist_id

    params = urllib.parse.urlencode({
        "part": "id,contentDetails",
        "forHandle": _CAZETV_HANDLE,
        "key": api_key,
    })
    data = _get(f"{_YT_CHANNELS}?{params}")
    if "error" in data:
        logger.error("[YouTube] erro ao resolver canal: %s", data["error"])
        return None, None
    items = data.get("items", [])
    if not items:
        logger.warning("[YouTube] handle @%s não encontrado", _CAZETV_HANDLE)
        return None, None

    _resolved_channel_id = items[0]["id"]
    _uploads_playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    logger.info("[YouTube] canal resolvido: @%s → %s (uploads: %s)",
                _CAZETV_HANDLE, _resolved_channel_id, _uploads_playlist_id)
    return _resolved_channel_id, _uploads_playlist_id


def _get_recent_video_ids(api_key: str, playlist_id: str, max_results: int = 10) -> list[str]:
    """Retorna IDs dos vídeos mais recentes do canal via playlistItems."""
    params = urllib.parse.urlencode({
        "part": "contentDetails",
        "playlistId": playlist_id,
        "maxResults": max_results,
        "key": api_key,
    })
    data = _get(f"{_YT_PLAYLIST}?{params}")
    if "error" in data:
        logger.error("[YouTube] erro ao buscar playlist: %s", data["error"])
        return []
    ids = [i["contentDetails"]["videoId"] for i in data.get("items", [])]
    logger.info("[YouTube] playlistItems → %d vídeo(s)", len(ids))
    return ids


def _get_live_details(api_key: str, video_ids: list[str]) -> list[dict]:
    """Retorna lista de {id, title, actualStartTime, actualEndTime, scheduledStartTime}."""
    params = urllib.parse.urlencode({
        "part": "snippet,liveStreamingDetails",
        "id": ",".join(video_ids),
        "key": api_key,
    })
    items = _get(f"{_YT_VIDEOS}?{params}").get("items", [])
    result = []
    for item in items:
        details = item.get("liveStreamingDetails", {})
        title = item.get("snippet", {}).get("title", "?")
        entry = {
            "id": item["id"],
            "title": title,
            "actualStart": details.get("actualStartTime"),
            "actualEnd": details.get("actualEndTime"),
            "scheduled": details.get("scheduledStartTime"),
        }
        logger.info(
            "[YouTube] vídeo %s | %s | actualStart=%s | actualEnd=%s | scheduled=%s",
            entry["id"], title, entry["actualStart"], entry["actualEnd"], entry["scheduled"],
        )
        result.append(entry)
    return result


def _fmt_horario(dt: datetime) -> str:
    return dt.astimezone(_BRT).strftime("%H:%M BRT")


def get_cazetv_live(game_ts: int | None = None) -> str | None:
    """Retorna URL da live ativa ou agendada da CazeTV.

    Usa playlistItems (uploads) em vez de search para evitar restrições de IP.
    Prioridade: live ativa agora > stream agendada mais próxima do jogo.
    """
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        logger.warning("[YouTube] YOUTUBE_API_KEY não configurada — link da CazeTV desativado")
        return None

    logger.info("[YouTube] iniciando busca (game_ts=%s)", game_ts)

    try:
        _cid, playlist_id = _resolve_channel(api_key)
        if not playlist_id:
            return None

        video_ids = _get_recent_video_ids(api_key, playlist_id, max_results=15)
        if not video_ids:
            logger.info("[YouTube] nenhum vídeo encontrado no canal")
            return None

        videos = _get_live_details(api_key, video_ids)

        game_dt = datetime.fromtimestamp(game_ts, tz=timezone.utc) if game_ts else datetime.now(timezone.utc)

        # 1. Live ativa agora (actualStartTime presente, sem actualEndTime)
        live_now = [v for v in videos if v["actualStart"] and not v["actualEnd"]]
        if live_now:
            best = live_now[0]
            url = f"https://www.youtube.com/watch?v={best['id']}"
            logger.info("[YouTube] live ativa: %s | %s", best["id"], best["title"])
            return url

        # 2. Stream agendada (scheduledStartTime, sem actualStartTime ou actualEndTime)
        scheduled = [
            v for v in videos
            if v["scheduled"] and not v["actualStart"] and not v["actualEnd"]
        ]
        if scheduled:
            def _sched_dt(v):
                return datetime.fromisoformat(v["scheduled"].replace("Z", "+00:00"))

            best = min(scheduled, key=lambda v: abs((_sched_dt(v) - game_dt).total_seconds()))
            horario = _fmt_horario(_sched_dt(best))
            url = f"https://www.youtube.com/watch?v={best['id']}"
            logger.info("[YouTube] stream agendada mais próxima: %s (%s) | %s", best["id"], horario, best["title"])
            return f"{url} (prevista às {horario})"

        logger.info("[YouTube] nenhuma live ativa ou agendada encontrada")
        return None

    except Exception as e:
        logger.exception("[YouTube] falha ao buscar live da CazeTV: %s", e)
        return None
