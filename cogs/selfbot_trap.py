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
    get_all_selfbot_log_channels,
    get_selfbot_channel,
    remove_selfbot_channel,
    remove_selfbot_log_channel,
    set_selfbot_channel,
    set_selfbot_log_channel,
)

logger = logging.getLogger(__name__)

# Janela de tempo para varredura de mensagens suspeitas (segundos)
SWEEP_WINDOW = 15

# Cache de mensagens recentes: user_id -> deque de (guild_id, channel_id, message_id, timestamp)
_recent: dict[int, deque] = defaultdict(lambda: deque(maxlen=100))

# Canal trap por guild: guild_id -> channel_id (cache em memória)
_trap_channels: dict[int, int] = {}
_log_channels: dict[int, int] = {}


class SelfbotTrapCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        global _trap_channels, _log_channels
        _trap_channels = await get_all_selfbot_channels()
        _log_channels = await get_all_selfbot_log_channels()

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

        try:
            await message.delete()
        except discord.Forbidden:
            logger.warning("Sem permissão para deletar mensagem do canal armadilha")
        except Exception:
            logger.exception("Erro ao deletar mensagem do canal armadilha")

        now = time.time()
        to_delete: list[tuple[int, int]] = []
        for entry in list(_recent[member.id]):
            g_id, ch_id, msg_id, ts = entry
            if g_id != guild.id:
                continue
            if ch_id == message.channel.id:
                continue
            if now - ts <= SWEEP_WINDOW:
                to_delete.append((ch_id, msg_id))

        if to_delete:
            logger.info("Varrendo %d mensagens recentes de %s em outros canais", len(to_delete), member)
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

        _recent.pop(member.id, None)

        kicked = False
        if isinstance(member, discord.Member):
            try:
                await member.kick(reason="Selfbot detectado: enviou mensagem em canal armadilha")
                kicked = True
                logger.info("Usuário %s kickado por selfbot em %s", member, guild.name)
            except discord.Forbidden:
                logger.warning("Sem permissão para kickar %s em %s", member, guild.name)
            except Exception:
                logger.exception("Erro ao kickar usuário selfbot")

        # Envia log no canal configurado ou fallback para canal do sistema
        log_channel_id = _log_channels.get(guild.id)
        log_ch = self.bot.get_channel(log_channel_id) if log_channel_id else guild.system_channel

        if log_ch:
            try:
                if log_channel_id:
                    embed = discord.Embed(
                        title="🚨 Selfbot Detectado",
                        color=0xFF4444,
                        timestamp=discord.utils.utcnow(),
                    )
                    embed.add_field(name="Usuário", value=f"{member} (`{member.id}`)", inline=True)
                    embed.add_field(name="Canal armadilha", value=message.channel.mention, inline=True)
                    embed.add_field(name="Mensagens varridas", value=str(len(to_delete)), inline=True)
                    embed.add_field(name="Ação", value="✅ Kickado" if kicked else "⚠️ Sem permissão para kickar", inline=True)
                    embed.set_thumbnail(url=member.display_avatar.url)
                    await log_ch.send(embed=embed)
                else:
                    await log_ch.send(
                        f"🚨 **Selfbot detectado e removido:** `{member}` ({member.id})\n"
                        f"Kickado automaticamente. {len(to_delete)} mensagem(ns) apagada(s) em outros canais."
                    )
            except Exception:
                logger.exception("Erro ao enviar log de selfbot")

    # ── Slash commands ────────────────────────────────────────────────────────

    @app_commands.command(
        name="config-selfbot",
        description="Configura a armadilha de selfbot (apenas admins)",
    )
    @app_commands.describe(
        canal="Canal armadilha — qualquer mensagem aqui dispara o kick",
        canal_log="(Opcional) Canal onde o bot registra cada detecção com detalhes",
    )
    @app_commands.default_permissions(administrator=True)
    async def cmd_config_selfbot(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel,
        canal_log: discord.TextChannel | None = None,
    ) -> None:
        guild_id = interaction.guild_id
        await set_selfbot_channel(guild_id, canal.id)
        _trap_channels[guild_id] = canal.id

        if canal_log:
            await set_selfbot_log_channel(guild_id, canal_log.id)
            _log_channels[guild_id] = canal_log.id

        embed = discord.Embed(title="🚨 Armadilha de Selfbot Configurada", color=0xFF4444)
        embed.add_field(name="Canal armadilha", value=canal.mention, inline=True)

        if canal_log:
            embed.add_field(name="Canal de log", value=canal_log.mention, inline=True)

        embed.add_field(
            name="Como funciona",
            value=(
                "Qualquer usuário que enviar uma mensagem no canal armadilha será **kickado automaticamente**.\n"
                f"O bot também varre mensagens dos últimos **{SWEEP_WINDOW}s** em outros canais e as apaga.\n\n"
                "⚠️ Não envie mensagens no canal armadilha!"
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
        await remove_selfbot_log_channel(guild_id)
        _trap_channels.pop(guild_id, None)
        _log_channels.pop(guild_id, None)
        await interaction.response.send_message(
            "✅ Armadilha de selfbot e canal de log removidos.", ephemeral=True
        )

    @app_commands.command(
        name="selfbot-status",
        description="Mostra o status da armadilha de selfbot (apenas admins)",
    )
    @app_commands.default_permissions(administrator=True)
    async def cmd_selfbot_status(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id
        channel_id = _trap_channels.get(guild_id)
        log_channel_id = _log_channels.get(guild_id)

        embed = discord.Embed(title="🚨 Status — Armadilha de Selfbot", color=0xFF4444)
        if channel_id:
            ch = interaction.guild.get_channel(channel_id)
            mention = ch.mention if ch else f"<canal removido: {channel_id}>"
            embed.add_field(name="Status", value="🟢 Ativo", inline=True)
            embed.add_field(name="Canal armadilha", value=mention, inline=True)
            embed.add_field(name="Janela de varredura", value=f"{SWEEP_WINDOW}s", inline=True)

            if log_channel_id:
                log_ch = interaction.guild.get_channel(log_channel_id)
                log_mention = log_ch.mention if log_ch else f"<canal removido: {log_channel_id}>"
                embed.add_field(name="Canal de log", value=log_mention, inline=True)
            else:
                embed.add_field(name="Canal de log", value="Canal do sistema (padrão)", inline=True)
        else:
            embed.add_field(name="Status", value="🔴 Inativo", inline=True)
            embed.description = "Use `/config-selfbot` para configurar."

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SelfbotTrapCog(bot))
