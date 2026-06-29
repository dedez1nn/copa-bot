"""Cog Dev — comandos de teste (admin) para disparar cada embed individualmente."""

import asyncio
import io
import time
import logging
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from services import bracket

from cogs.copa import (
    _embed_artilharia,
    _embed_jogos_rodada,
    _embed_team,
    _embed_resumo_diario,
)
from services import copa as copa_svc
from services import youtube
from services.copa import BRT
from services.copa_monitor import (
    build_lineup_embed,
    build_pre_game_embed,
    build_kickoff_embed,
    build_ht_embed,
    build_2ht_embed,
    build_final_embed,
)

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

_FAKE_LIVE_DATA = {
    "Period": 4,
    "MatchStatus": 1,
    "HomeTeam": {
        "Score": 2,
        "Goals": [
            {"IdPlayer": 1010, "Minute": 23, "Period": 3, "Type": 1},
            {"IdPlayer": 1011, "Minute": 44, "Period": 3, "Type": 1},
        ],
        "Bookings": [
            {"IdPlayer": 1006, "Minute": 38, "Card": 1},
        ],
        "Substitutions": [
            {"IdPlayerOn": 1007, "IdPlayerOff": 1008, "Minute": 46},
        ],
        "Players": [_fp(pid, n, name, pos) for pid, n, name, pos in [
            (1001,  1, "Alisson", 0), (1002, 2, "Danilo", 1),
            (1003,  3, "Marquinhos", 1), (1004, 4, "G. Magalhães", 1),
            (1005,  6, "G. Arana", 1), (1006, 5, "Casemiro", 2),
            (1007,  8, "Bruno G.", 2), (1008, 10, "Paquetá", 2),
            (1009, 11, "Rodrygo", 3), (1010, 7, "Vinícius Jr", 3),
            (1011, 19, "Raphinha", 3),
        ]],
    },
    "AwayTeam": {
        "Score": 1,
        "Goals": [
            {"IdPlayer": 2011, "Minute": 31, "Period": 3, "Type": 1},
        ],
        "Bookings": [
            {"IdPlayer": 2009, "Minute": 55, "Card": 2},
        ],
        "Substitutions": [
            {"IdPlayerOn": 2007, "IdPlayerOff": 2008, "Minute": 58},
            {"IdPlayerOn": 2005, "IdPlayerOff": 2006, "Minute": 72},
        ],
        "Players": [_fp(pid, n, name, pos) for pid, n, name, pos in [
            (2001, 23, "Dibu Martínez", 0), (2002, 26, "N. Molina", 1),
            (2003, 13, "C. Romero", 1), (2004,  5, "L. Martínez", 1),
            (2005,  3, "Tagliafico", 1), (2006,  7, "De Paul", 2),
            (2007, 24, "E. Fernández", 2), (2008, 20, "Mac Allister", 2),
            (2009, 11, "Di María", 3), (2010, 22, "L. Martínez", 3),
            (2011, 10, "Messi", 3),
        ]],
    },
    "Statistics": [
        {"Type": 1, "HomeValue": 58, "AwayValue": 42},
        {"Type": 2, "HomeValue": 12, "AwayValue": 7},
        {"Type": 3, "HomeValue": 5, "AwayValue": 3},
        {"Type": 4, "HomeValue": 6, "AwayValue": 3},
        {"Type": 5, "HomeValue": 9, "AwayValue": 14},
    ],
}

_FAKE_LIVE_MATCH = _fm("brazil", "Brasil", "argentina", "Argentina", "inprogress", 2, 1, -3300, "Grupo B")

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
        name="teste-artilharia",
        description="[TESTE] Embed de artilharia (dados falsos)",
    )
    @app_commands.default_permissions(administrator=True)
    async def teste_artilharia(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await interaction.followup.send(embed=_embed_artilharia(_FAKE_ARTILHARIA))

    @app_commands.command(
        name="teste-chaveamento",
        description="[TESTE] Imagem do chaveamento do mata-mata (dados reais da FIFA)",
    )
    @app_commands.default_permissions(administrator=True)
    async def teste_chaveamento(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            png = await asyncio.to_thread(bracket.render_bracket_png)
        except Exception:
            logger.exception("Falha ao gerar chaveamento")
            await interaction.followup.send("❌ Falha ao gerar o chaveamento.", ephemeral=True)
            return
        embed = discord.Embed(title="🗺️ Chaveamento — Copa 2026", color=0xFFCD46)
        embed.set_image(url="attachment://chaveamento.png")
        await interaction.followup.send(
            embed=embed,
            file=discord.File(io.BytesIO(png), filename="chaveamento.png"),
        )

    @app_commands.command(
        name="avancar",
        description="[TESTE] Avança uma seleção uma rodada no chaveamento (simulação)",
    )
    @app_commands.describe(selecao="Nome da seleção (ex: Brasil, Canadá, Argentina)")
    @app_commands.default_permissions(administrator=True)
    async def avancar(self, interaction: discord.Interaction, selecao: str) -> None:
        await interaction.response.defer()
        ok, msg = await asyncio.to_thread(bracket.advance_team, selecao)
        if not ok:
            await interaction.followup.send(msg, ephemeral=True)
            return
        try:
            png = await asyncio.to_thread(bracket.render_bracket_png)
        except Exception:
            logger.exception("Falha ao gerar chaveamento após avanço")
            await interaction.followup.send(f"{msg}\n⚠️ Falha ao gerar a imagem.", ephemeral=True)
            return
        embed = discord.Embed(title="🗺️ Chaveamento — Copa 2026 (simulação)",
                              description=msg, color=0xFFCD46)
        embed.set_image(url="attachment://chaveamento.png")
        await interaction.followup.send(
            embed=embed,
            file=discord.File(io.BytesIO(png), filename="chaveamento.png"),
        )

    @app_commands.command(
        name="avancar-reset",
        description="[TESTE] Zera a simulação de avanço, voltando ao estado real da FIFA",
    )
    @app_commands.default_permissions(administrator=True)
    async def avancar_reset(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await asyncio.to_thread(bracket.reset_overrides)
        await interaction.followup.send("♻️ Simulação zerada — chaveamento voltou ao estado real da API.", ephemeral=True)

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
        embed = _embed_resumo_diario(jogos, datetime.now(BRT))
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="teste-aviso-1hora", description="[TESTE] Aviso de 1 hora para a partida mais próxima")
    @app_commands.default_permissions(administrator=True)
    async def teste_aviso_1hora(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        m = await _partida_proxima()
        if not m:
            await interaction.followup.send("❌ Nenhuma partida encontrada.", ephemeral=True)
            return
        live_url = await asyncio.to_thread(youtube.get_cazetv_live, m["date_ts"])
        embed = build_pre_game_embed(m, 60, live_url)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="teste-aviso-30min", description="[TESTE] Aviso de 30 minutos para a partida mais próxima")
    @app_commands.default_permissions(administrator=True)
    async def teste_aviso_30min(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        m = await _partida_proxima()
        if not m:
            await interaction.followup.send("❌ Nenhuma partida encontrada.", ephemeral=True)
            return
        live_url = await asyncio.to_thread(youtube.get_cazetv_live, m["date_ts"])
        embed = build_pre_game_embed(m, 30, live_url)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="teste-inicio", description="[TESTE] Notificação de início de jogo")
    @app_commands.default_permissions(administrator=True)
    async def teste_inicio(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        m = await _partida_proxima()
        if not m:
            await interaction.followup.send("❌ Nenhuma partida encontrada.", ephemeral=True)
            return
        await interaction.followup.send(embed=build_kickoff_embed(m))

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
        await interaction.followup.send(embed=build_ht_embed(m, h, a))

    @app_commands.command(name="teste-inicio-2t", description="[TESTE] Notificação de início do 2º tempo (dados falsos)")
    @app_commands.default_permissions(administrator=True)
    async def teste_inicio_2t(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        embed = build_2ht_embed(_FAKE_LIVE_MATCH, 2, 1, _FAKE_LIVE_DATA)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="teste-fim-jogo", description="[TESTE] Notificação de fim de jogo (dados falsos)")
    @app_commands.default_permissions(administrator=True)
    async def teste_fim_jogo(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        embed = build_final_embed(_FAKE_LIVE_MATCH, 2, 1, _FAKE_LIVE_DATA)
        await interaction.followup.send(embed=embed)

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
