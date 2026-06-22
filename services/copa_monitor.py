"""Monitoramento ao vivo e de escalações — Copa 2026 Discord (fonte: FIFA API)."""

import asyncio
import logging
import time
from datetime import datetime

import discord

from services.copa import (
    BRT, EN_TO_PT, FLAGS,
    _load_fifa_live, _player_map_fifa,
    get_jogos_rodada, is_brazil_match, flag,
)

logger = logging.getLogger(__name__)

_watch: dict[str, dict] = {}

LINEUP_INTERVAL_BRAZIL = 10
LINEUP_INTERVAL_OTHER = 60
LINEUP_WINDOW_SECS = 3600
VAR_WINDOW_SECS = 600

_POS_FIFA = {0: "GK", 1: "DEF", 2: "MEI", 3: "ATA"}


def _state(key: str) -> dict:
    if key not in _watch:
        _watch[key] = {
            "announced_30": False,
            "kicked_off": False,
            "primed": False,
            "ht_sent": False,
            "2ht_sent": False,
            "final_sent": False,
            "lineup_sent": False,
            "last_lineup_check": 0.0,
            "last_period": None,
            "seen_goals": set(),
            "seen_cards": set(),
            "last_status": None,
            "suspended": False,
            "pending_goals": {},
            "pending_cards": {},
        }
    return _watch[key]


# ── Embed de escalação ────────────────────────────────────────────────────────

def _fmt_player(p: dict, pmap: dict[str, str]) -> str:
    num = p.get("ShirtNumber", "?")
    pid = str(p.get("IdPlayer") or "")
    name = pmap.get(pid, "?")
    pos = _POS_FIFA.get(p.get("Position"), "?")
    return f"`{num:>2}` **{name}** • {pos}"


def build_lineup_embed(m: dict, data: dict) -> discord.Embed | None:
    home_data = data.get("HomeTeam") or {}
    away_data = data.get("AwayTeam") or {}
    home_xi = [p for p in (home_data.get("Players") or []) if p.get("Status") == 1]
    away_xi = [p for p in (away_data.get("Players") or []) if p.get("Status") == 1]

    if not home_xi and not away_xi:
        return None

    pmap = _player_map_fifa(home_data, away_data)
    home_form = home_data.get("Tactics") or ""
    away_form = away_data.get("Tactics") or ""
    hora = datetime.fromtimestamp(m["date_ts"], tz=BRT).strftime("%H:%M")
    grupo = m.get("group") or m.get("stage") or ""
    is_brazil = is_brazil_match(m)
    hf = flag(m["home_en"])
    af = flag(m["away_en"])

    embed = discord.Embed(
        title=f"📋 Escalação — {hf} {m['home_pt']} x {m['away_pt']} {af}",
        description=f"**{grupo}** • {hora} BRT",
        color=0x009C3B if is_brazil else 0xFFD700,
    )

    home_label = f"🏠 {hf} {m['home_pt']}"
    away_label = f"✈️ {af} {m['away_pt']}"
    if home_form:
        home_label += f" • {home_form}"
    if away_form:
        away_label += f" • {away_form}"

    embed.add_field(
        name=home_label,
        value="\n".join(_fmt_player(p, pmap) for p in home_xi) or "—",
        inline=True,
    )
    embed.add_field(
        name=away_label,
        value="\n".join(_fmt_player(p, pmap) for p in away_xi) or "—",
        inline=True,
    )
    embed.set_footer(text="Escalação confirmada · via FIFA")
    embed.timestamp = discord.utils.utcnow()
    return embed


# ── Envio ─────────────────────────────────────────────────────────────────────

async def _send_all(bot: discord.Client, channels: list[tuple[int, int]], **kwargs) -> None:
    for guild_id, channel_id in channels:
        ch = bot.get_channel(channel_id)
        if ch is None:
            continue
        try:
            await ch.send(**kwargs)
        except Exception:
            logger.exception("Falha ao enviar para canal %s (guild %s)", channel_id, guild_id)


# ── Verificação ao vivo via FIFA ──────────────────────────────────────────────

async def _check_fifa_live(bot, channels, m: dict, st: dict) -> None:
    data = await asyncio.to_thread(_load_fifa_live, m["fifa_id"])
    if not data:
        st["fifa_was_blocked"] = True
        return

    home = data.get("HomeTeam") or {}
    away = data.get("AwayTeam") or {}
    period = data.get("Period")
    match_status = data.get("MatchStatus")
    h_score = home.get("Score", 0) or 0
    a_score = away.get("Score", 0) or 0
    home_pt = m["home_pt"]
    away_pt = m["away_pt"]
    pmap = _player_map_fifa(home, away)

    if st.get("fifa_was_blocked"):
        st["fifa_was_blocked"] = False
        for side in (home, away):
            for g in (side.get("Goals") or []):
                st["seen_goals"].add((g.get("IdPlayer"), g.get("Minute"), g.get("Period"), g.get("Type")))
            for b in (side.get("Bookings") or []):
                st["seen_cards"].add((b.get("IdPlayer"), b.get("Minute"), b.get("Card")))
        st["last_period"] = period
        if period is not None and period > 3:
            st["ht_sent"] = True
        if period is not None and period >= 4:
            st["2ht_sent"] = True
        return

    # -- conjuntos atuais para checar remoções via VAR --
    all_goals_raw = [(home_pt, g) for g in (home.get("Goals") or [])] + \
                    [(away_pt, g) for g in (away.get("Goals") or [])]
    current_goal_keys: set = {
        (g.get("IdPlayer"), g.get("Minute"), g.get("Period"), g.get("Type"))
        for _, g in all_goals_raw
    }

    all_cards_raw = [(home_pt, b) for b in (home.get("Bookings") or [])] + \
                    [(away_pt, b) for b in (away.get("Bookings") or [])]
    current_red_keys: set = {
        (b.get("IdPlayer"), b.get("Minute"), b.get("Card"))
        for _, b in all_cards_raw if b.get("Card") == 2
    }

    # -- novos gols --
    for team_pt, g in all_goals_raw:
        gkey = (g.get("IdPlayer"), g.get("Minute"), g.get("Period"), g.get("Type"))
        if gkey in st["seen_goals"]:
            continue
        st["seen_goals"].add(gkey)
        player = pmap.get(str(g.get("IdPlayer") or ""), "?")
        minute = g.get("Minute", "?")
        gtype = g.get("Type", 2)
        extra = " (contra)" if gtype == 3 else (" (pen)" if gtype == 4 else "")
        st["pending_goals"][gkey] = {
            "ts": time.time(), "player": player, "team_pt": team_pt,
            "minute": minute, "extra": extra,
            "score_at_announce": (h_score, a_score),
        }
        await _send_all(
            bot, channels,
            content=(
                f"⚽ **GOL! {player}{extra}** ({team_pt}) — {minute}'\n"
                f"**{home_pt} {h_score}-{a_score} {away_pt}**"
            ),
        )

    # -- novos cartões vermelhos --
    for team_pt, b in all_cards_raw:
        ckey = (b.get("IdPlayer"), b.get("Minute"), b.get("Card"))
        if ckey in st["seen_cards"]:
            continue
        st["seen_cards"].add(ckey)
        if b.get("Card") != 2:
            continue
        player = pmap.get(str(b.get("IdPlayer") or ""), "?")
        minute = b.get("Minute", "?")
        st["pending_cards"][ckey] = {
            "ts": time.time(), "player": player, "team_pt": team_pt, "minute": minute,
        }
        await _send_all(bot, channels, content=f"🟥 **{player}** ({team_pt}) — {minute}'")

    # -- VAR: gol removido da API → anulado --
    now_var = time.time()
    for gkey, info in list(st["pending_goals"].items()):
        if gkey not in current_goal_keys:
            del st["pending_goals"][gkey]
            prev_h, prev_a = info.get("score_at_announce", (None, None))
            score_changed = (prev_h, prev_a) != (h_score, a_score)
            if not score_changed:
                # placar igual → API corrigiu dado errado, não foi VAR
                continue
            await _send_all(
                bot, channels,
                content=(
                    f"🚫 **Gol anulado pelo VAR!** {info['player']}{info['extra']} "
                    f"({info['team_pt']}) — {info['minute']}'\n"
                    f"**{home_pt} {h_score}-{a_score} {away_pt}**"
                ),
            )
        elif now_var - info["ts"] >= VAR_WINDOW_SECS:
            del st["pending_goals"][gkey]

    # -- VAR: vermelho removido da API → revertido --
    for ckey, info in list(st["pending_cards"].items()):
        if ckey not in current_red_keys:
            del st["pending_cards"][ckey]
            await _send_all(
                bot, channels,
                content=(
                    f"🟨 **Vermelho revertido pelo VAR!** {info['player']} "
                    f"({info['team_pt']}) — {info['minute']}'"
                ),
            )
        elif now_var - info["ts"] >= VAR_WINDOW_SECS:
            del st["pending_cards"][ckey]

    # -- transições de período --
    last = st["last_period"]
    if period != last:
        if last == 3 and not st["ht_sent"]:
            st["ht_sent"] = True
            await _send_all(
                bot, channels,
                content=f"🔔 **Fim do 1º tempo**\n**{home_pt} {h_score}-{a_score} {away_pt}**",
            )
        if period == 4 and not st["2ht_sent"]:
            has_2t = any(
                g.get("Period") == 4
                for side in (home, away)
                for g in (side.get("Goals") or []) + (side.get("Bookings") or [])
            )
            if has_2t:
                st["2ht_sent"] = True
                await _send_all(
                    bot, channels,
                    content=f"🔔 **2º tempo começou!**\n**{home_pt} {h_score}-{a_score} {away_pt}**",
                )
        st["last_period"] = period

    if not st["final_sent"] and match_status == 0 and st["kicked_off"]:
        st["final_sent"] = True
        await _send_all(
            bot, channels,
            content=f"🏁 **Fim de jogo!**\n**{home_pt} {h_score}-{a_score} {away_pt}**",
        )


# ── Verificação de escalação ──────────────────────────────────────────────────

async def check_lineup(m: dict) -> discord.Embed | None:
    if not m.get("fifa_id"):
        return None
    data = await asyncio.to_thread(_load_fifa_live, m["fifa_id"])
    if not data:
        return None
    return build_lineup_embed(m, data)


# ── Job principal (chamado a cada 10s pelo cog) ───────────────────────────────

async def run_monitor_tick(bot: discord.Client, channels: list[tuple[int, int]]) -> None:
    if not channels:
        return

    try:
        matches = await asyncio.to_thread(get_jogos_rodada)
    except Exception:
        logger.exception("Erro ao buscar jogos da rodada")
        return

    now = time.time()

    for m in matches:
        if not m.get("fifa_id"):
            continue

        key = m["fifa_id"]
        st = _state(key)
        ts = m["date_ts"]
        status = m["status"]
        hf = flag(m["home_en"])
        af = flag(m["away_en"])

        # ── Suspensão / retomada ──
        _last = st["last_status"]
        if _last == "inprogress" and status == "notstarted" and st["kicked_off"] and not st["final_sent"]:
            st["suspended"] = True
            await _send_all(
                bot, channels,
                content=f"⚠️ **Jogo suspenso temporariamente!**\n⚽ **{hf} {m['home_pt']} x {m['away_pt']} {af}**",
            )
        elif st.get("suspended") and status == "inprogress":
            st["suspended"] = False
            h = m["home_score"] if m["home_score"] is not None else 0
            a = m["away_score"] if m["away_score"] is not None else 0
            await _send_all(
                bot, channels,
                content=f"▶️ **Jogo retomado!**\n⚽ **{hf} {m['home_pt']} {h}-{a} {m['away_pt']} {af}**",
            )
        st["last_status"] = status

        # ── Aviso 30 min antes ──
        if not st["announced_30"] and status == "notstarted" and 0 < (ts - now) <= 1800:
            st["announced_30"] = True
            mins = max(1, int((ts - now) / 60))
            await _send_all(
                bot, channels,
                content=(
                    f"⏰ **Em {mins} minuto{'s' if mins != 1 else ''}!**\n"
                    f"⚽ **{hf} {m['home_pt']} x {m['away_pt']} {af}**"
                ),
            )

        # ── Escalação (1h antes) ──
        if not st["lineup_sent"] and status == "notstarted" and 0 < (ts - now) <= LINEUP_WINDOW_SECS:
            interval = LINEUP_INTERVAL_BRAZIL if is_brazil_match(m) else LINEUP_INTERVAL_OTHER
            if (now - st["last_lineup_check"]) >= interval:
                st["last_lineup_check"] = now
                embed = await check_lineup(m)
                if embed:
                    st["lineup_sent"] = True
                    for guild_id, channel_id in channels:
                        ch = bot.get_channel(channel_id)
                        if ch is None:
                            continue
                        try:
                            await ch.send(
                                content=(
                                    f"📋 **Escalação divulgada!** "
                                    f"{hf} {m['home_pt']} x {m['away_pt']} {af}"
                                ),
                                embed=embed,
                            )
                        except Exception:
                            logger.exception("Erro ao enviar embed de escalação")

        # ── Priming ──
        if not st["primed"] and status == "inprogress":
            st["kicked_off"] = True
            data = await asyncio.to_thread(_load_fifa_live, m["fifa_id"])
            if data:
                home = data.get("HomeTeam") or {}
                away = data.get("AwayTeam") or {}
                for side in (home, away):
                    for g in (side.get("Goals") or []):
                        st["seen_goals"].add((g.get("IdPlayer"), g.get("Minute"), g.get("Period"), g.get("Type")))
                    for b in (side.get("Bookings") or []):
                        st["seen_cards"].add((b.get("IdPlayer"), b.get("Minute"), b.get("Card")))
                period = data.get("Period")
                st["last_period"] = period
                if period is not None and period > 3:
                    st["ht_sent"] = True
                if period is not None and period >= 4:
                    st["2ht_sent"] = True
                st["primed"] = True
            continue

        # ── Início ──
        if not st["kicked_off"] and status == "inprogress":
            st["kicked_off"] = True
            st["primed"] = True
            await _send_all(
                bot, channels,
                content=f"🔔 **Começou!**\n⚽ **{hf} {m['home_pt']} 0-0 {m['away_pt']} {af}**",
            )

        # ── Ao vivo ──
        if status == "inprogress":
            await _check_fifa_live(bot, channels, m, st)

        # ── Fim de jogo ──
        if not st["final_sent"] and status == "finished" and st["kicked_off"]:
            st["final_sent"] = True
            h = m["home_score"] if m["home_score"] is not None else "?"
            a = m["away_score"] if m["away_score"] is not None else "?"
            await _send_all(
                bot, channels,
                content=f"🏁 **Fim de jogo!**\n**{m['home_pt']} {h}-{a} {m['away_pt']}**",
            )
