import os
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.db.session import engine, AsyncSessionLocal
from app.db.base import Base

logger = logging.getLogger(__name__)

# Importar todos los modelos para que SQLAlchemy los registre antes de create_all
import app.acceso_registro.models    # noqa: F401  (User, Vehiculo, Taller, PasswordResetCode)
import app.emergencias.models        # noqa: F401  (Incidente, Evidencia)
import app.talleres_tecnicos.models  # noqa: F401  (Tecnico, Asignacion)
import app.cotizacion_pagos.models   # noqa: F401  (Cotizacion, Pago)
import app.comunicacion.models       # noqa: F401  (Mensaje)
import app.reportes.models           # noqa: F401  (BitacoraEvento)
import app.notificaciones.models     # noqa: F401  (DispositivoToken)

from app.acceso_registro.router  import router as acceso_router
from app.talleres_tecnicos.router import router as talleres_router
from app.emergencias.router      import router as emergencias_router
from app.solicitudes.router      import router as solicitudes_router
from app.cotizacion_pagos.router import router as pagos_router
from app.comunicacion.router     import router as comunicacion_router
from app.reportes.router         import router as reportes_router
from app.ia.router               import router as ia_router
from app.notificaciones.router   import router as notificaciones_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Directorios de uploads para §4.4
    os.makedirs("uploads/fotos", exist_ok=True)
    os.makedirs("uploads/audio", exist_ok=True)

    # Retry en conexión inicial — Railway puede tardar unos segundos en tener la DB lista
    for attempt in range(10):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                await conn.execute(text(
                    "ALTER TABLE tecnicos ADD COLUMN IF NOT EXISTS latitud DOUBLE PRECISION"
                ))
                await conn.execute(text(
                    "ALTER TABLE tecnicos ADD COLUMN IF NOT EXISTS longitud DOUBLE PRECISION"
                ))
                await conn.execute(text(
                    "ALTER TABLE tecnicos ADD COLUMN IF NOT EXISTS ultima_actualizacion TIMESTAMP WITH TIME ZONE"
                ))
                await conn.execute(text(
                    "ALTER TABLE incidentes ADD COLUMN IF NOT EXISTS tipo_incidente VARCHAR(50)"
                ))
            break
        except Exception as exc:
            if attempt == 9:
                raise
            wait = 2 ** attempt  # 1, 2, 4, 8, 16, 32 … segundos
            logger.warning("DB no disponible (intento %d/10): %s — reintentando en %ds", attempt + 1, exc, wait)
            await asyncio.sleep(wait)

    # Pool warm-up
    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT 1"))

    # §4.5 – Inicializar modelos IA en startup
    from app.ia import clasificador
    clasificador.inicializar()

    yield


app = FastAPI(title="Taller Backend", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Servir archivos estáticos de evidencias (§4.4)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

app.include_router(acceso_router,       prefix="/api/acceso",       tags=["Acceso y Registro"])
app.include_router(talleres_router,     prefix="/api/talleres",     tags=["Talleres y Técnicos"])
app.include_router(emergencias_router,  prefix="/api/emergencias",  tags=["Emergencias"])
app.include_router(solicitudes_router,  prefix="/api/solicitudes",  tags=["Solicitudes"])
app.include_router(pagos_router,        prefix="/api/pagos",        tags=["Cotización y Pagos"])
app.include_router(comunicacion_router, prefix="/api/comunicacion", tags=["Comunicación"])
app.include_router(reportes_router,     prefix="/api/reportes",     tags=["Reportes"])
app.include_router(ia_router,           prefix="/api/ia",           tags=["Inteligencia Artificial"])
app.include_router(notificaciones_router, prefix="/api/notificaciones", tags=["Notificaciones"])


@app.get("/")
async def root():
    return {"message": "Taller Backend v2.0 — IA activa"}
