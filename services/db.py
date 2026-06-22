"""Camada de dados — MongoDB via Motor (async)."""

import os
import logging
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


def _col(name: str):
    return _db[name]


async def init_db() -> None:
    global _client, _db
    uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
    db_name = os.environ.get("MONGO_INITDB_DATABASE", "copa_discord")
    _client = AsyncIOMotorClient(uri)
    _db = _client[db_name]
    await _db["copa_channels"].create_index("guild_id", unique=True)
    await _db["selfbot_trap_channels"].create_index("guild_id", unique=True)
    await _db["selfbot_log_channels"].create_index("guild_id", unique=True)
    await _db["command_channels"].create_index("guild_id", unique=True)
    logger.info("MongoDB conectado: %s", uri.split("@")[-1])


# ── Copa channels ─────────────────────────────────────────────────────────────

async def get_copa_channel(guild_id: int) -> int | None:
    doc = await _col("copa_channels").find_one({"guild_id": guild_id})
    return doc["channel_id"] if doc else None


async def set_copa_channel(guild_id: int, channel_id: int) -> None:
    await _col("copa_channels").update_one(
        {"guild_id": guild_id},
        {"$set": {"channel_id": channel_id}},
        upsert=True,
    )


async def get_all_copa_channels() -> list[tuple[int, int]]:
    cursor = _col("copa_channels").find({})
    return [(doc["guild_id"], doc["channel_id"]) async for doc in cursor]


# ── Selfbot trap channels ─────────────────────────────────────────────────────

async def get_selfbot_channel(guild_id: int) -> int | None:
    doc = await _col("selfbot_trap_channels").find_one({"guild_id": guild_id})
    return doc["channel_id"] if doc else None


async def set_selfbot_channel(guild_id: int, channel_id: int) -> None:
    await _col("selfbot_trap_channels").update_one(
        {"guild_id": guild_id},
        {"$set": {"channel_id": channel_id}},
        upsert=True,
    )


async def remove_selfbot_channel(guild_id: int) -> None:
    await _col("selfbot_trap_channels").delete_one({"guild_id": guild_id})


async def get_all_selfbot_channels() -> dict[int, int]:
    cursor = _col("selfbot_trap_channels").find({})
    return {doc["guild_id"]: doc["channel_id"] async for doc in cursor}


# ── Selfbot log channels ──────────────────────────────────────────────────────

async def get_selfbot_log_channel(guild_id: int) -> int | None:
    doc = await _col("selfbot_log_channels").find_one({"guild_id": guild_id})
    return doc["channel_id"] if doc else None


async def set_selfbot_log_channel(guild_id: int, channel_id: int) -> None:
    await _col("selfbot_log_channels").update_one(
        {"guild_id": guild_id},
        {"$set": {"channel_id": channel_id}},
        upsert=True,
    )


async def remove_selfbot_log_channel(guild_id: int) -> None:
    await _col("selfbot_log_channels").delete_one({"guild_id": guild_id})


async def get_all_selfbot_log_channels() -> dict[int, int]:
    cursor = _col("selfbot_log_channels").find({})
    return {doc["guild_id"]: doc["channel_id"] async for doc in cursor}


# ── Command channels (canal-fenrir) ──────────────────────────────────────────

async def get_command_channel(guild_id: int) -> int | None:
    doc = await _col("command_channels").find_one({"guild_id": guild_id})
    return doc["channel_id"] if doc else None


async def set_command_channel(guild_id: int, channel_id: int) -> None:
    await _col("command_channels").update_one(
        {"guild_id": guild_id},
        {"$set": {"channel_id": channel_id}},
        upsert=True,
    )


async def remove_command_channel(guild_id: int) -> None:
    await _col("command_channels").delete_one({"guild_id": guild_id})


async def get_all_command_channels() -> dict[int, int]:
    cursor = _col("command_channels").find({})
    return {doc["guild_id"]: doc["channel_id"] async for doc in cursor}
