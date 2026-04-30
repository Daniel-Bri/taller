from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

_connect_args = {"ssl": "require"} if settings.db_ssl else {}

engine = create_async_engine(
    settings.async_database_url,
    echo=False,
    pool_pre_ping=True,   # detecta conexiones muertas antes de usarlas (crítico con reload=True)
    pool_recycle=1800,    # recicla conexiones cada 30 min (5 min era demasiado agresivo en dev)
    pool_size=5,
    max_overflow=10,
    connect_args=_connect_args,
)

AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
