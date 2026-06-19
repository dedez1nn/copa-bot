import aiosqlite
from pathlib import Path

DB_PATH = Path.home() / ".local" / "share" / "copa-discord" / "bot.db"


async def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS copa_channels (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS selfbot_trap_channels (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL
            )
        """)
        await db.commit()


async def get_copa_channel(guild_id: int) -> int | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT channel_id FROM copa_channels WHERE guild_id = ?", (guild_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def set_copa_channel(guild_id: int, channel_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO copa_channels (guild_id, channel_id) VALUES (?, ?)",
            (guild_id, channel_id),
        )
        await db.commit()


async def get_all_copa_channels() -> list[tuple[int, int]]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT guild_id, channel_id FROM copa_channels")
        return await cursor.fetchall()


async def get_selfbot_channel(guild_id: int) -> int | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT channel_id FROM selfbot_trap_channels WHERE guild_id = ?", (guild_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def set_selfbot_channel(guild_id: int, channel_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO selfbot_trap_channels (guild_id, channel_id) VALUES (?, ?)",
            (guild_id, channel_id),
        )
        await db.commit()


async def remove_selfbot_channel(guild_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM selfbot_trap_channels WHERE guild_id = ?", (guild_id,)
        )
        await db.commit()


async def get_all_selfbot_channels() -> dict[int, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT guild_id, channel_id FROM selfbot_trap_channels")
        rows = await cursor.fetchall()
        return {guild_id: channel_id for guild_id, channel_id in rows}
