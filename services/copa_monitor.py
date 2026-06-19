"""Monitoramento ao vivo e de escalações — Copa 2026 Discord."""

import asyncio
import logging
import time
from datetime import datetime

import discord

from services.copa import (
    BRT, SS_TO_PT, FLAGS,
    _load_incidents, _load_lineups, _load_fifa_live, _player_map_fifa,
    get_jogos_rodada, is_brazil_match, flag,
)

logger = logging.getLogger(__name__)

# Estado por partida
_watch: dict[str, dict] = {}

LINEUP_INTERVAL_BRAZIL = 10   # segundos
LINEUP_INTERVAL_OTHER = 60    # segundos
LINEUP_WINDOW_SECS = 3600     # começa verificar 1h antes


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
        }
    return _watch[key]


# ── Embed de escalação ────────────────────────────────────────────────────────

_POS_MAP = {
    "GK": "GK", "G": "GK",
    "DC": "DEF", "DL": "DEF", "DR": "DEF", "D": "DEF",
    "WB": "LAT", "WBL": "LAT", "WBR": "LAT",
    "MC": "MEI", "ML": "MEI", "MR": "MEI", "M": "MEI",
    "AM": "MEI", "DM": "VOL",
    "FW": "ATA", "FWL": "ATA", "FWR": "ATA", "F": "ATA",
    "SS": "SAG",
}


def _fmt_player_ss(entry: dict) -> str:
    p = entry.get("player", {})
    num = p.get("jerseyNumber", "?")
    name = p.get("shortName") or p.get("name") or "?"
    pos = _POS_MAP.get((entry.get("position") or "").upper(), entry.get("position") or "?")
    return f"`{num:>2}` **{name}** • {pos}"


def _fmt_player_fifa(entry: dict, pmap: dict[str, str]) -> str:
    _POS_FIFA = {0: "GK", 1: "DEF", 2: "MEI", 3: "ATA"}
    num = entry.get("ShirtNumber", "?")
    pid = entry.get("IdPlayer")
    name = pmap.get(str(pid), "?") if pid else "?"
    pos = _POS_FIFA.get(entry.get("Position"), "?")
    return f"`{num:>2}` **{name}** • {pos}"


def build_lineup_embed(m: dict, data_ss: dict | None, data_fifa: dict | None) -> discord.Embed | None:
    home_flag = flag(m["home_en"])
    away_flag = flag(m["away_en"])
    is_brazil = is_brazil_match(m)
    hora = datetime.fromtimestamp(m["date_ts"], tz=BRT).strftime("%H:%M")
    grupo = m.get("group") or m.get("stage") or ""

    home_players: list[str] = []
    away_players: list[str] = []
    home_form = ""
    away_form = ""
    confirmed = False
    source = ""

    if data_ss and (data_ss.get("home") or data_ss.get("away")):
        home_data = data_ss.get("home") or {}
        away_data = data_ss.get("away") or {}
        home_xi = [p for p in home_data.get("players", []) if not p.get("substitute")]
        away_xi = [p for p in away_data.get("players", []) if not p.get("substitute")]
        if not home_xi and not away_xi:
            return None
        home_form = home_data.get("formation", "")
        away_form = away_data.get("formation", "")
        confirmed = data_ss.get("confirmed", False)
        home_players = [_fmt_player_ss(p) for p in home_xi]
        away_players = [_fmt_player_ss(p) for p in away_xi]
        source = "Sofascore"

    elif data_fifa:
        home_data = data_fifa.get("HomeTeam") or {}
        away_data = data_fifa.get("AwayTeam") or {}
        home_xi = [p for p in (home_data.get("Players") or []) if p.get("Status") == 1]
        away_xi = [p for p in (away_data.get("Players") or []) if p.get("Status") == 1]
        if not home_xi and not away_xi:
            return None
        home_form = home_data.get("Tactics") or ""
        away_form = away_data.get("Tactics") or ""
        confirmed = True
        pmap = _player_map_fifa(home_data, away_data)
        home_players = [_fmt_player_fifa(p, pmap) for p in home_xi]
        away_players = [_fmt_player_fifa(p, pmap) for p in away_xi]
        source = "FIFA"

    else:
        return None

    color = 0x009C3B if is_brazil else 0xFFD700

    home_label = f"{home_flag} {m['home_pt']}"
    away_label = f"{away_flag} {m['away_pt']}"
    if home_form:
        home_label += f" • {home_form}"
    if away_form:
        away_label += f" • {away_form}"

    embed = discord.Embed(
        title=f"📋 Escalação — {home_flag} {m['home_pt']} x {m['away_pt']} {away_flag}",
        description=f"**{grupo}** • {hora} BRT",
        color=color,
    )
    embed.add_field(
        name=f"🏠 {home_label}",
        value="\n".join(home_players) or "—",
        inline=True,
    )
    embed.add_field(
        name=f"✈️ {away_label}",
        value="\n".join(away_players) or "—",
        inline=True,
    )

    status_icon = "✅ Confirmada" if confirmed else "⚠️ Provável"
    embed.set_footer(text=f"{status_icon} · via {source}")
    embed.timestamp = discord.utils.utcnow()
    return embed


# ── Verificação de escalação ──────────────────────────────────────────────────

async def check_lineup(m: dict, st: dict) -> discord.Embed | None:
    ss_data = None
    fifa_data = None

    if m.get("ss_id"):
        ss_data = await asyncio.to_thread(_load_lineups, m["ss_id"])
    if m.get("fifa_id") and not ss_data:
        fifa_data = await asyncio.to_thread(_load_fifa_live, m["fifa_id"])

    embed = build_lineup_embed(m, ss_data, fifa_data)
    return embed


# ── Envio de notificações ao vivo ─────────────────────────────────────────────

async def _send_all(bot: discord.Client, channels: list[tuple[int, int]], **kwargs) -> None:
    for guild_id, channel_id in channels:
        ch = bot.get_channel(channel_id)
        if ch is None:
            continue
        try:
            await ch.send(**kwargs)
        except Exception:
            logger.exception("Falha ao enviar para canal %s (guild %s)", channel_id, guild_id)


async def _check_ss_live(bot, channels, m: dict, st: dict) -> None:
    data = await asyncio.to_thread(_load_incidents, m["ss_id"])
    if not data:
        st["ss_was_blocked"] = True
        return

    incs = data.get("incidents") or []

    if st.get("ss_was_blocked"):
        st["ss_was_blocked"] = False
        for inc in incs:
            inc_id = inc.get("id")
            if not inc_id:
                continue
            itype = inc.get("incidentType")
            if itype == "goal":
                st["seen_goals"].add(inc_id)
            elif itype == "card":
                st["seen_cards"].add(inc_id)
            code = (inc.get("text") or "").upper()
            if code == "HT":
                st["ht_sent"] = True
            elif code in ("2HT", "SH"):
                st["2ht_sent"] = True
        return

    home_pt = m["home_pt"]
    away_pt = m["away_pt"]
    h = m["home_score"] if m["home_score"] is not None else 0
    a = m["away_score"] if m["away_score"] is not None else 0

    if st["ht_sent"] and not st["2ht_sent"]:
        live_mins = [
            inc.get("time", 0) for inc in incs
            if inc.get("incidentType") in ("goal", "card", "substitution")
        ]
        if live_mins and max(live_mins) > 45:
            st["2ht_sent"] = True
            await _send_all(
                bot, channels,
                content=f"🔔 **2º tempo começou!**\n⚽ **{home_pt} {h}-{a} {away_pt}**",
            )

    for inc in incs:
        inc_id = inc.get("id")
        if not inc_id:
            continue
        itype = inc.get("incidentType")

        if itype == "goal" and inc_id not in st["seen_goals"]:
            st["seen_goals"].add(inc_id)
            p_obj = inc.get("player") or {}
            player = p_obj.get("shortName") or p_obj.get("name") or "?"
            team = SS_TO_PT.get((inc.get("team") or {}).get("name", "").lower(), "?")
            min_ = inc.get("time", "?")
            extra = " (contra)" if inc.get("isOwnGoal") else (
                " (pen)" if inc.get("incidentClass") == "penalty" else ""
            )
            await _send_all(
                bot, channels,
                content=(
                    f"⚽ **GOL! {player}{extra}** ({team}) — {min_}'\n"
                    f"**{home_pt} {h}-{a} {away_pt}**"
                ),
            )

        elif itype == "card" and inc_id not in st["seen_cards"]:
            st["seen_cards"].add(inc_id)
            if inc.get("incidentClass") == "red":
                p_obj = inc.get("player") or {}
                player = p_obj.get("shortName") or p_obj.get("name") or "?"
                team = SS_TO_PT.get((inc.get("team") or {}).get("name", "").lower(), "?")
                min_ = inc.get("time", "?")
                await _send_all(
                    bot, channels,
                    content=f"🟥 **{player}** ({team}) — {min_}'",
                )

        elif itype == "period":
            code = (inc.get("text") or "").upper()
            if code == "HT" and not st["ht_sent"]:
                st["ht_sent"] = True
                await _send_all(
                    bot, channels,
                    content=f"🔔 **Fim do 1º tempo**\n**{home_pt} {h}-{a} {away_pt}**",
                )


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

    all_goals = [(home_pt, g) for g in (home.get("Goals") or [])] + \
                [(away_pt, g) for g in (away.get("Goals") or [])]
    for team_pt, g in all_goals:
        gkey = (g.get("IdPlayer"), g.get("Minute"), g.get("Period"), g.get("Type"))
        if gkey in st["seen_goals"]:
            continue
        st["seen_goals"].add(gkey)
        player = pmap.get(str(g.get("IdPlayer") or ""), "?")
        minute = g.get("Minute", "?")
        gtype = g.get("Type", 2)
        extra = " (contra)" if gtype == 3 else (" (pen)" if gtype == 4 else "")
        await _send_all(
            bot, channels,
            content=(
                f"⚽ **GOL! {player}{extra}** ({team_pt}) — {minute}'\n"
                f"**{home_pt} {h_score}-{a_score} {away_pt}**"
            ),
        )

    all_cards = [(home_pt, b) for b in (home.get("Bookings") or [])] + \
                [(away_pt, b) for b in (away.get("Bookings") or [])]
    for team_pt, b in all_cards:
        ckey = (b.get("IdPlayer"), b.get("Minute"), b.get("Card"))
        if ckey in st["seen_cards"]:
            continue
        st["seen_cards"].add(ckey)
        if b.get("Card") != 2:
            continue
        player = pmap.get(str(b.get("IdPlayer") or ""), "?")
        minute = b.get("Minute", "?")
        await _send_all(
            bot, channels,
            content=f"🟥 **{player}** ({team_pt}) — {minute}'",
        )

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
        key = str(m.get("ss_id") or m.get("fifa_id") or "")
        if not key:
            continue

        st = _state(key)
        ts = m["date_ts"]
        status = m["status"]

        # ── Aviso 30 min antes ──
        if not st["announced_30"] and status == "notstarted" and 0 < (ts - now) <= 1800:
            st["announced_30"] = True
            mins = max(1, int((ts - now) / 60))
            home_flag = flag(m["home_en"])
            away_flag = flag(m["away_en"])
            await _send_all(
                bot, channels,
                content=(
                    f"⏰ **Em {mins} minuto{'s' if mins != 1 else ''}!**\n"
                    f"⚽ **{home_flag} {m['home_pt']} x {m['away_pt']} {away_flag}**"
                ),
            )

        # ── Verificar escalação (1h antes) ──
        if not st["lineup_sent"] and status == "notstarted" and 0 < (ts - now) <= LINEUP_WINDOW_SECS:
            is_br = is_brazil_match(m)
            interval = LINEUP_INTERVAL_BRAZIL if is_br else LINEUP_INTERVAL_OTHER
            if (now - st["last_lineup_check"]) >= interval:
                st["last_lineup_check"] = now
                embed = await check_lineup(m, st)
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
                                    f"{flag(m['home_en'])} {m['home_pt']} x "
                                    f"{m['away_pt']} {flag(m['away_en'])}"
                                ),
                                embed=embed,
                            )
                        except Exception:
                            logger.exception("Erro ao enviar embed de escalação")

        # ── Priming (bot iniciou com jogo já ao vivo) ──
        if not st["primed"] and status == "inprogress":
            st["kicked_off"] = True
            ok = False
            if m.get("ss_id"):
                data = await asyncio.to_thread(_load_incidents, m["ss_id"])
                if data:
                    for inc in (data.get("incidents") or []):
                        inc_id = inc.get("id")
                        if inc_id:
                            itype = inc.get("incidentType")
                            if itype == "goal":
                                st["seen_goals"].add(inc_id)
                            elif itype == "card":
                                st["seen_cards"].add(inc_id)
                    ok = True
            elif m.get("fifa_id"):
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
                    ok = True
            if ok:
                st["primed"] = True
            continue

        # ── Início do jogo ──
        if not st["kicked_off"] and status == "inprogress":
            st["kicked_off"] = True
            st["primed"] = True
            home_flag = flag(m["home_en"])
            away_flag = flag(m["away_en"])
            await _send_all(
                bot, channels,
                content=(
                    f"🔔 **Começou!**\n"
                    f"⚽ **{home_flag} {m['home_pt']} 0-0 {m['away_pt']} {away_flag}**"
                ),
            )

        # ── Ao vivo ──
        if status == "inprogress":
            if m.get("ss_id"):
                await _check_ss_live(bot, channels, m, st)
            elif m.get("fifa_id"):
                await _check_fifa_live(bot, channels, m, st)

        # ── Fim de jogo ──
        if not st["final_sent"] and status == "finished" and st["kicked_off"]:
            st["final_sent"] = True
            h = m["home_score"] if m["home_score"] is not None else "?"
            a = m["away_score"] if m["away_score"] is not None else "?"
            await _send_all(
                bot, channels,
                content=(
                    f"🏁 **Fim de jogo!**\n"
                    f"**{m['home_pt']} {h}-{a} {m['away_pt']}**"
                ),
            )
