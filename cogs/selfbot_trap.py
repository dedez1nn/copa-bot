"""Cog Armadilha de Selfbot — detecção e resposta automática."""

import asyncio
import logging
import time
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import commands

from services.db import (
    get_all_selfbot_channels,
    get_selfbot_channel,
    remove_selfbot_channel,
    set_selfbot_channel,
)

logger = logging.getLogger(__name__)

# Janela de tempo para varredura de mensagens suspeitas (segundos)
SWEEP_WINDOW = 15

# Cache de mensagens recentes: user_id -> deque de (guild_id, channel_id, message_id, timestamp)
_recent: dict[int, deque] = defaultdict(lambda: deque(maxlen=100))

# Canal trap por guild: guild_id -> channel_id (cache em memória)
_trap_channels: dict[int, int] = {}


class SelfbotTrapCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        global _trap_channels
        _trap_channels = await get_all_selfbot_channels()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return

        guild_id = message.guild.id
        trap_channel_id = _trap_channels.get(guild_id)

        # Registra mensagem no cache de todos os servidores com trap ativo
        if trap_channel_id is not None:
            _recent[message.author.id].append((
                guild_id,
                message.channel.id,
                message.id,
                time.time(),
            ))

        # Se a mensagem foi enviada no canal armadilha → acionar trap
        if trap_channel_id is not None and message.channel.id == trap_channel_id:
            await self._trigger_trap(message)

    async def _trigger_trap(self, message: discord.Message) -> None:
        guild = message.guild
        member = message.author

        logger.warning(
            "Selfbot detectado: %s (%s) em %s/%s",
            member, member.id, guild.name, message.channel.name,
        )

        # Tenta deletar a mensagem do canal armadilha
        try:
            await message.delete()
        except discord.Forbidden:
            logger.warning("Sem permissão para deletar mensagem do canal armadilha")
        except Exception:
            logger.exception("Erro ao deletar mensagem do canal armadilha")

        # Varredura: busca mensagens recentes do mesmo usuário em outros canais
        now = time.time()
        to_delete: list[tuple[int, int]] = []  # (channel_id, message_id)
        for entry in list(_recent[member.id]):
            g_id, ch_id, msg_id, ts = entry
            if g_id != guild.id:
                continue
            if ch_id == message.channel.id:
                continue
            if now - ts <= SWEEP_WINDOW:
                to_delete.append((ch_id, msg_id))

        if to_delete:
            logger.info(
                "Varrendo %d mensagens recentes de %s em outros canais", len(to_delete), member
            )
            for ch_id, msg_id in to_delete:
                ch = guild.get_channel(ch_id)
                if ch is None:
                    continue
                try:
                    msg = await ch.fetch_message(msg_id)
                    await msg.delete()
                except discord.NotFound:
                    pass
                except discord.Forbidden:
                    logger.warning("Sem permissão para deletar mensagem em %s", ch.name)
                except Exception:
                    logger.exception("Erro ao deletar mensagem no sweep")

        # Limpa o cache do usuário
        _recent.pop(member.id, None)

        # Tenta kickar o usuário
        if not isinstance(member, discord.Member):
            return

        try:
            await member.kick(reason="Selfbot detectado: enviou mensagem em canal armadilha")
            logger.info("Usuário %s kickado por selfbot em %s", member, guild.name)
        except discord.Forbidden:
            logger.warning("Sem permissão para kickar %s em %s", member, guild.name)
        except Exception:
            logger.exception("Erro ao kickar usuário selfbot")

        # Aviso no log do servidor (canal sistema se configurado)
        try:
            system_ch = guild.system_channel
            if system_ch:
                await system_ch.send(
                    f"🚨 **Selfbot detectado e removido:** `{member}` ({member.id})\n"
                    f"O usuário foi kickado automaticamente e {len(to_delete)} "
                    f"mensagem(ns) foram apagadas em outros canais."
                )
        except Exception:
            pass

    # ── Slash commands ────────────────────────────────────────────────────────

    @app_commands.command(
        name="config-selfbot",
        description="Configura o canal armadilha de selfbot (apenas admins)",
    )
    @app_commands.describe(canal="Canal que servirá de armadilha — qualquer mensagem dispara o kick")
    @app_commands.default_permissions(administrator=True)
    async def cmd_config_selfbot(
        self, interaction: discord.Interaction, canal: discord.TextChannel
    ) -> None:
        guild_id = interaction.guild_id
        await set_selfbot_channel(guild_id, canal.id)
        _trap_channels[guild_id] = canal.id

        embed = discord.Embed(
            title="🚨 Armadilha de Selfbot Configurada",
            color=0xFF4444,
        )
        embed.add_field(
            name="Canal armadilha",
            value=canal.mention,
            inline=False,
        )
        embed.add_field(
            name="Como funciona",
            value=(
                "Qualquer usuário que enviar uma mensagem nesse canal será **kickado automaticamente**.\n"
                f"O bot também varre mensagens enviadas em outros canais nos últimos **{SWEEP_WINDOW} segundos** "
                "e as apaga.\n\n"
                "⚠️ Não envie mensagens nesse canal!"
            ),
            inline=False,
        )
        embed.set_footer(text="Use /selfbot-status para ver a configuração atual")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="selfbot-remover",
        description="Remove a armadilha de selfbot deste servidor (apenas admins)",
    )
    @app_commands.default_permissions(administrator=True)
    async def cmd_selfbot_remover(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id
        await remove_selfbot_channel(guild_id)
        _trap_channels.pop(guild_id, None)
        await interaction.response.send_message(
            "✅ Armadilha de selfbot removida.", ephemeral=True
        )

    @app_commands.command(
        name="selfbot-status",
        description="Mostra o status da armadilha de selfbot (apenas admins)",
    )
    @app_commands.default_permissions(administrator=True)
    async def cmd_selfbot_status(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id
        channel_id = _trap_channels.get(guild_id)

        embed = discord.Embed(title="🚨 Status — Armadilha de Selfbot", color=0xFF4444)
        if channel_id:
            ch = interaction.guild.get_channel(channel_id)
            mention = ch.mention if ch else f"<canal removido: {channel_id}>"
            embed.add_field(name="Status", value="🟢 Ativo", inline=True)
            embed.add_field(name="Canal armadilha", value=mention, inline=True)
            embed.add_field(
                name="Janela de varredura",
                value=f"{SWEEP_WINDOW} segundos",
                inline=True,
            )
        else:
            embed.add_field(name="Status", value="🔴 Inativo", inline=True)
            embed.description = "Use `/config-selfbot` para configurar."

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SelfbotTrapCog(bot))
