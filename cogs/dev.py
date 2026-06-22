"""Cog Dev — comandos de teste (admin) para disparar cada embed individualmente."""

import asyncio
import time
import logging
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from cogs.copa import (
    _embed_artilharia,
    _embed_grupo,
    _embed_jogos_rodada,
    _embed_team,
)
from services import copa as copa_svc
from services import youtube
from services.copa import BRT
from services.copa_monitor import build_lineup_embed

logger = logging.getLogger(__name__)

# ── Helpers ──────────────────────────────────────────────────────────────────

async def _partida_proxima() -> dict | None:
    """Retorna a partida mais próxima do momento atual via API real."""
    try:
        matches = await asyncio.to_thread(copa_svc.get_jogos_rodada)
    except Exception:
        return None
    inprogress = [m for m in matches if m["status"] == "inprogress"]
    if inprogress:
        return inprogress[0]
    upcoming = sorted([m for m in matches if m["status"] == "notstarted"], key=lambda m: m["date_ts"])
    if upcoming:
        return upcoming[0]
    finished = sorted([m for m in matches if m["status"] == "finished"], key=lambda m: -m["date_ts"])
    return finished[0] if finished else None


def _jogo(m: dict) -> str:
    hf = copa_svc.flag(m["home_en"])
    af = copa_svc.flag(m["away_en"])
    return f"{hf} **{m['home_pt']} x {m['away_pt']}** {af}"


# ── Helpers para dados falsos ─────────────────────────────────────────────────

def _fm(home_en, home_pt, away_en, away_pt, status, h=None, a=None, delta=0, group="Grupo B"):
    return {
        "home_en": home_en, "home_pt": home_pt,
        "away_en": away_en, "away_pt": away_pt,
        "date_ts": int(time.time()) + delta,
        "status": status,
        "home_score": h, "away_score": a,
        "group": group, "stage": "",
        "fifa_id": "fake",
    }


def _fp(pid, num, name, pos):
    return {
        "Status": 1, "ShirtNumber": num, "IdPlayer": pid, "Position": pos,
        "ShortName": [{"Locale": "pt-BR", "Description": name}],
    }


# ── Dados falsos ──────────────────────────────────────────────────────────────

_FAKE_RODADA = [
    _fm("brazil",    "Brasil",     "argentina", "Argentina", "inprogress", 1, 1,  -2700, "Grupo B"),
    _fm("france",    "França",     "germany",   "Alemanha",  "notstarted", None, None, 3600, "Grupo D"),
    _fm("spain",     "Espanha",    "portugal",  "Portugal",  "finished",   2, 1, -86400, "Grupo C"),
    _fm("england",   "Inglaterra", "netherlands","Holanda",  "notstarted", None, None, 7200, "Grupo E"),
    _fm("usa",       "EUA",        "mexico",    "México",    "finished",   0, 0, -43200, "Grupo A"),
]

_FAKE_TIME_MATCHES = [
    _fm("brazil", "Brasil", "argentina", "Argentina", "inprogress", 1, 1,   -2700,  "Grupo B"),
    _fm("brazil", "Brasil", "france",    "França",    "finished",   2, 0,  -172800, "Grupo B"),
    _fm("brazil", "Brasil", "germany",   "Alemanha",  "notstarted", None, None, 86400, "Grupo B"),
]

_FAKE_GRUPO = [
    _fm("brazil",    "Brasil",     "argentina", "Argentina", "inprogress", 1, 1,  -2700),
    _fm("france",    "França",     "germany",   "Alemanha",  "notstarted", None, None, 3600),
    _fm("brazil",    "Brasil",     "france",    "França",    "finished",   2, 0, -172800),
    _fm("argentina", "Argentina",  "germany",   "Alemanha",  "finished",   1, 0,  -86400),
]

_FAKE_ARTILHARIA = [
    {"name": "Vinícius Jr",    "team": "Brasil",     "goals": 4},
    {"name": "Kylian Mbappé",  "team": "França",     "goals": 3},
    {"name": "Lionel Messi",   "team": "Argentina",  "goals": 3},
    {"name": "Lamine Yamal",   "team": "Espanha",    "goals": 2},
    {"name": "Harry Kane",     "team": "Inglaterra", "goals": 2},
    {"name": "Rodrygo",        "team": "Brasil",     "goals": 2},
    {"name": "Bukayo Saka",    "team": "Inglaterra", "goals": 1},
    {"name": "Raphinha",       "team": "Brasil",     "goals": 1},
    {"name": "Memphis Depay",  "team": "Holanda",    "goals": 1},
    {"name": "Erling Haaland", "team": "Noruega",    "goals": 1},
]

_FAKE_ESCALACAO_MATCH = _fm("brazil", "Brasil", "argentina", "Argentina", "notstarted", None, None, 3600)

_FAKE_ESCALACAO_DATA = {
    "HomeTeam": {
        "Tactics": "4-3-3",
        "Players": [
            _fp(1001,  1, "Alisson",          0),
            _fp(1002,  2, "Danilo",            1),
            _fp(1003,  3, "Marquinhos",        1),
            _fp(1004,  4, "G. Magalhães",      1),
            _fp(1005,  6, "Guilherme Arana",   1),
            _fp(1006,  5, "Casemiro",          2),
            _fp(1007,  8, "Bruno Guimarães",   2),
            _fp(1008, 10, "Lucas Paquetá",     2),
            _fp(1009, 11, "Rodrygo",           3),
            _fp(1010,  7, "Vinícius Jr",       3),
            _fp(1011, 19, "Raphinha",          3),
        ],
    },
    "AwayTeam": {
        "Tactics": "4-2-3-1",
        "Players": [
            _fp(2001, 23, "Dibu Martínez",       0),
            _fp(2002, 26, "Nahuel Molina",        1),
            _fp(2003, 13, "C. Romero",            1),
            _fp(2004,  5, "Lisandro Martínez",    1),
            _fp(2005,  3, "Nicolás Tagliafico",   1),
            _fp(2006,  7, "Rodrigo De Paul",      2),
            _fp(2007, 24, "Enzo Fernández",       2),
            _fp(2008, 20, "Alexis Mac Allister",  2),
            _fp(2009, 11, "Ángel Di María",       3),
            _fp(2010, 22, "Lautaro Martínez",     3),
            _fp(2011, 10, "Lionel Messi",         3),
        ],
    },
}


# ── Cog ───────────────────────────────────────────────────────────────────────

class DevCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="teste-rodada",
        description="[TESTE] Embed de jogos da rodada (dados falsos)",
    )
    @app_commands.default_permissions(administrator=True)
    async def teste_rodada(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await interaction.followup.send(embed=_embed_jogos_rodada(_FAKE_RODADA))

    @app_commands.command(
        name="teste-time",
        description="[TESTE] Embed de jogos de uma seleção (dados falsos — Brasil)",
    )
    @app_commands.default_permissions(administrator=True)
    async def teste_time(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await interaction.followup.send(embed=_embed_team("brasil", _FAKE_TIME_MATCHES))

    @app_commands.command(
        name="teste-grupo",
        description="[TESTE] Embed de jogos de um grupo (dados falsos — Grupo B)",
    )
    @app_commands.default_permissions(administrator=True)
    async def teste_grupo(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await interaction.followup.send(embed=_embed_grupo("B", _FAKE_GRUPO))

    @app_commands.command(
        name="teste-artilharia",
        description="[TESTE] Embed de artilharia (dados falsos)",
    )
    @app_commands.default_permissions(administrator=True)
    async def teste_artilharia(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await interaction.followup.send(embed=_embed_artilharia(_FAKE_ARTILHARIA))

    @app_commands.command(
        name="teste-escalacao",
        description="[TESTE] Embed de escalação Brasil x Argentina (dados falsos)",
    )
    @app_commands.default_permissions(administrator=True)
    async def teste_escalacao(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        embed = build_lineup_embed(_FAKE_ESCALACAO_MATCH, _FAKE_ESCALACAO_DATA)
        if not embed:
            await interaction.followup.send("❌ Falha ao gerar embed.", ephemeral=True)
            return
        await interaction.followup.send(
            content="📋 **Escalação** — Brasil x Argentina",
            embed=embed,
        )


    # ── Notificações automáticas (partida mais próxima) ──────────────────────

    @app_commands.command(name="teste-resumo-diario", description="[TESTE] Resumo dos jogos de hoje")
    @app_commands.default_permissions(administrator=True)
    async def teste_resumo_diario(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            jogos = await asyncio.to_thread(copa_svc.get_jogos_hoje)
        except Exception:
            await interaction.followup.send("❌ Erro ao buscar jogos.", ephemeral=True)
            return
        if not jogos:
            await interaction.followup.send("Nenhum jogo agendado para hoje.", ephemeral=True)
            return
        linhas = ["📅 **Jogos de hoje — Copa 2026**\n"]
        for m in sorted(jogos, key=lambda x: x["date_ts"]):
            hora = datetime.fromtimestamp(m["date_ts"], tz=BRT).strftime("%H:%M")
            grupo = m.get("group") or m.get("stage") or ""
            hf = copa_svc.flag(m["home_en"])
            af = copa_svc.flag(m["away_en"])
            linhas.append(f"⚽ **{hf} {m['home_pt']} x {m['away_pt']} {af}**  {hora} BRT  *{grupo}*")
        await interaction.followup.send("\n".join(linhas))

    @app_commands.command(name="teste-aviso-1hora", description="[TESTE] Aviso de 1 hora para a partida mais próxima")
    @app_commands.default_permissions(administrator=True)
    async def teste_aviso_1hora(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        m = await _partida_proxima()
        if not m:
            await interaction.followup.send("❌ Nenhuma partida encontrada.", ephemeral=True)
            return
        live_url = await asyncio.to_thread(youtube.get_cazetv_live, m["date_ts"])
        live_line = f"\n📺 **Assista ao vivo:** {live_url}" if live_url else ""
        await interaction.followup.send(
            f"⏰ **Em 60 minutos!**\n⚽ {_jogo(m)}{live_line}"
        )

    @app_commands.command(name="teste-aviso-30min", description="[TESTE] Aviso de 30 minutos para a partida mais próxima")
    @app_commands.default_permissions(administrator=True)
    async def teste_aviso_30min(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        m = await _partida_proxima()
        if not m:
            await interaction.followup.send("❌ Nenhuma partida encontrada.", ephemeral=True)
            return
        live_url = await asyncio.to_thread(youtube.get_cazetv_live, m["date_ts"])
        live_line = f"\n📺 **Assista ao vivo:** {live_url}" if live_url else ""
        await interaction.followup.send(
            f"⏰ **Em 30 minutos!**\n⚽ {_jogo(m)}{live_line}"
        )

    @app_commands.command(name="teste-inicio", description="[TESTE] Notificação de início de jogo")
    @app_commands.default_permissions(administrator=True)
    async def teste_inicio(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        m = await _partida_proxima()
        if not m:
            await interaction.followup.send("❌ Nenhuma partida encontrada.", ephemeral=True)
            return
        await interaction.followup.send(f"🔔 **Começou!**\n⚽ {_jogo(m)}")

    @app_commands.command(name="teste-fim-1t", description="[TESTE] Notificação de fim do 1º tempo")
    @app_commands.default_permissions(administrator=True)
    async def teste_fim_1t(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        m = await _partida_proxima()
        if not m:
            await interaction.followup.send("❌ Nenhuma partida encontrada.", ephemeral=True)
            return
        h = m["home_score"] or 0
        a = m["away_score"] or 0
        await interaction.followup.send(
            f"🔔 **Fim do 1º tempo**\n**{m['home_pt']} {h}-{a} {m['away_pt']}**"
        )

    @app_commands.command(name="teste-inicio-2t", description="[TESTE] Notificação de início do 2º tempo")
    @app_commands.default_permissions(administrator=True)
    async def teste_inicio_2t(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        m = await _partida_proxima()
        if not m:
            await interaction.followup.send("❌ Nenhuma partida encontrada.", ephemeral=True)
            return
        await interaction.followup.send(f"🔔 **2º tempo começou!**\n⚽ {_jogo(m)}")

    @app_commands.command(name="teste-fim-jogo", description="[TESTE] Notificação de fim de jogo")
    @app_commands.default_permissions(administrator=True)
    async def teste_fim_jogo(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        m = await _partida_proxima()
        if not m:
            await interaction.followup.send("❌ Nenhuma partida encontrada.", ephemeral=True)
            return
        h = m["home_score"] or 0
        a = m["away_score"] or 0
        await interaction.followup.send(
            f"🏁 **Fim de jogo!**\n**{m['home_pt']} {h}-{a} {m['away_pt']}**"
        )

    @app_commands.command(name="teste-suspenso", description="[TESTE] Notificação de jogo suspenso")
    @app_commands.default_permissions(administrator=True)
    async def teste_suspenso(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        m = await _partida_proxima()
        if not m:
            await interaction.followup.send("❌ Nenhuma partida encontrada.", ephemeral=True)
            return
        hf = copa_svc.flag(m["home_en"])
        af = copa_svc.flag(m["away_en"])
        await interaction.followup.send(
            f"⚠️ **Jogo suspenso temporariamente!**\n⚽ **{hf} {m['home_pt']} x {m['away_pt']} {af}**"
        )

    @app_commands.command(name="teste-retomado", description="[TESTE] Notificação de jogo retomado")
    @app_commands.default_permissions(administrator=True)
    async def teste_retomado(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        m = await _partida_proxima()
        if not m:
            await interaction.followup.send("❌ Nenhuma partida encontrada.", ephemeral=True)
            return
        hf = copa_svc.flag(m["home_en"])
        af = copa_svc.flag(m["away_en"])
        h = m["home_score"] or 0
        a = m["away_score"] or 0
        await interaction.followup.send(
            f"▶️ **Jogo retomado!**\n⚽ **{hf} {m['home_pt']} {h}-{a} {m['away_pt']} {af}**"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DevCog(bot))
