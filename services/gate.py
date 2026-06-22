"""Gate de canal — controla em quais canais os comandos públicos são permitidos."""

import discord

_command_channels: dict[int, int] = {}


def load(channels: dict[int, int]) -> None:
    global _command_channels
    _command_channels = channels


def set_channel(guild_id: int, channel_id: int) -> None:
    _command_channels[guild_id] = channel_id


def remove_channel(guild_id: int) -> None:
    _command_channels.pop(guild_id, None)


def get_channel(guild_id: int) -> int | None:
    return _command_channels.get(guild_id)


async def allowed(interaction: discord.Interaction) -> bool:
    """Retorna True se o comando pode ser executado no canal atual.
    Envia resposta ephemeral e retorna False caso contrário.
    Deve ser chamado antes de interaction.response.defer().
    """
    guild_id = interaction.guild_id
    if guild_id is None:
        return True
    member = interaction.user
    if isinstance(member, discord.Member) and member.guild_permissions.administrator:
        return True
    ch = _command_channels.get(guild_id)
    if ch is None or interaction.channel_id == ch:
        return True
    await interaction.response.send_message(
        f"❌ Use os comandos em <#{ch}>.", ephemeral=True
    )
    return False
