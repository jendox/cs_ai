from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

__all__ = (
    "SessionLocal",
)

DATABASE_URL = "sqlite+aiosqlite:///state.db"

engine = create_async_engine(
    DATABASE_URL,
    future=True,
    pool_pre_ping=True,
    connect_args={"timeout": 10, "check_same_thread": False},
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
