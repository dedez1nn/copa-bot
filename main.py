#!/usr/bin/env python3
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from services.db import init_db

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)

COGS = ["cogs.fenrir", "cogs.copa", "cogs.selfbot_trap", "cogs.dev"]

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True


class CopaBot(commands.Bot):
    def __init__(self):
        app_id_raw = os.environ.get("DISCORD_APP_ID")
        super().__init__(
            command_prefix="!",
            intents=intents,
            application_id=int(app_id_raw) if app_id_raw else None,
        )

    async def setup_hook(self) -> None:
        await init_db()
        for cog in COGS:
            await self.load_extension(cog)
        await self.tree.sync()
        logging.getLogger(__name__).info("Cogs carregados e comandos sincronizados.")

    async def on_ready(self) -> None:
        logging.getLogger(__name__).info("Bot online: %s (ID: %s)", self.user, self.user.id)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="Copa do Mundo 2026 🏆",
            )
        )


def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("Variável DISCORD_BOT_TOKEN não encontrada no .env")

    bot = CopaBot()
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
