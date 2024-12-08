import os
import logging
from sqlalchemy import delete
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
import redis.asyncio as redis
from dotenv import load_dotenv
from datetime import datetime, time
import pytz
from sqlalchemy.sql import func
import re
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey
from contextlib import asynccontextmanager

load_dotenv()

DEBUG_LEVEL = os.getenv("DEBUG_LEVEL").upper()

db_user = os.getenv("DB_USER")
db_password = os.getenv("DB_PASSWORD")
db_host = os.getenv("DB_HOST")
db_port = os.getenv("DB_PORT")
db_name = os.getenv("DB_NAME")

if db_password:
    DATABASE_URL = (
        f"postgresql+asyncpg://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    )
else:
    DATABASE_URL = f"postgresql+asyncpg://{db_user}@{db_host}:{db_port}/{db_name}"

logging.basicConfig(level=getattr(logging, DEBUG_LEVEL, logging.INFO))
logger = logging.getLogger(__name__)

pool_size = int(os.getenv("DB_POOL_SIZE"))
max_overflow = int(os.getenv("DB_MAX_OVERFLOW"))

engine = create_async_engine(
    DATABASE_URL,
    echo=(DEBUG_LEVEL == "DEBUG"),
    pool_size=pool_size,
    max_overflow=max_overflow,
)

async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()

bot = Bot(token=os.getenv("TOKEN"))
dp = Dispatcher(bot)

if DEBUG_LEVEL == "DEBUG":
    from aiogram.contrib.middlewares.logging import LoggingMiddleware

    dp.middleware.setup(LoggingMiddleware())

redis_host = os.getenv("REDIS_HOST")
redis_port = int(os.getenv("REDIS_PORT"))
redis_password = os.getenv("REDIS_PASSWORD")
redis_db = int(os.getenv("REDIS_DB"))

if redis_password:
    redis_client = redis.Redis(
        host=redis_host,
        port=redis_port,
        password=redis_password,
        db=redis_db,
        decode_responses=True,
    )
else:
    redis_client = redis.Redis(
        host=redis_host,
        port=redis_port,
        db=redis_db,
        decode_responses=True,
    )


@asynccontextmanager
async def get_db_session():
    async with async_session() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def on_startup(dispatcher):
    await init_db()


if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
