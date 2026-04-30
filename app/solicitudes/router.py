import math
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import undefer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.dependencies import require_role, get_current_user
from app.acceso_registro.models import User
from app.emergencias.models import Incidente, Evidencia
from app.talleres_tecnicos.models import Asignacion
from app.talleres_tecnicos.schemas import AsignacionResponse
from app.talleres_tecnicos.service import get_taller_by_user
from app.ia.motor_asignacion import calcular_score, haversine
from app.ia import clasificador

router = APIRouter()

_ESTADOS_CERRADOS = ["cancelado", "finalizado"]


class SolicitudDisponibleResponse(BaseModel):
    incidente_id: int
    latitud: Optional[float]
    longitud: Optional[float]
    descripcion: Optional[str]
    tipo_problema: str
    prioridad: str
    estado: str
    fotos_urls: list[str]
    tiene_audio: bool
    created_at: str
    es_sos: bool = False
    distancia_km: Optional[float] = None
    eta_estimado_min: Optional[int] = None
    score_ia: float = 0.0


class AceptarPayload(BaseModel):
    eta: Optional[int] = None


# ── CU18 – Asignaciones activas del cliente (para chat) ──────────────────
@router.get("/mis-asignaciones", response_model=list[AsignacionResponse])
async def mis_asignaciones_cliente(
    current_user: User = Depends(require_role("cliente")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Asignacion)
        .join(Incidente, Asignacion.incidente_id == Incidente.id)
        .where(
            Incidente.usuario_id == current_user.id,
            Asignacion.estado.notin_(_ESTADOS_CERRADOS),
        )
        .order_by(Asignacion.created_at.desc())
    )
    return [AsignacionResponse.model_validate(a) for a in result.scalars().all()]


# ── CU13 – Ver solicitudes disponibles (§4.6 motor IA + score) ──────────
@router.get("/disponibles", response_model=list[SolicitudDisponibleResponse])
async def disponibles(
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    taller = await get_taller_by_user(current_user.id, db)

    tiene_asignacion_activa = (
        exists()
        .where(
            and_(
                Asignacion.incidente_id == Incidente.id,
                Asignacion.estado.notin_(_ESTADOS_CERRADOS),
            )
        )
        .correlate(Incidente)
    )

    result = await db.execute(
        select(Incidente)
        .options(undefer(Incidente.tipo_incidente))
        .where(
            Incidente.estado == "pendiente",
            or_(
                and_(Incidente.latitud.isnot(None), Incidente.longitud.isnot(None)),
                Incidente.prioridad == "alta",
            ),
            ~tiene_asignacion_activa,
        )
        .order_by(Incidente.prioridad.desc(), Incidente.created_at.desc())
    )
    incidentes = list(result.scalars().all())

    if not incidentes:
        return []

    inc_ids = [i.id for i in incidentes]
    evid_res = await db.execute(
        select(Evidencia.incidente_id, Evidencia.url, Evidencia.tipo)
        .where(Evidencia.incidente_id.in_(inc_ids))
    )
    fotos_map: dict[int, list[str]] = {}
    audio_map: dict[int, bool] = {}
    for row in evid_res.all():
        if row[2] == "foto" and row[1]:
            fotos_map.setdefault(row[0], []).append(row[1])
        elif row[2] == "audio":
            audio_map[row[0]] = True

    resultado: list[SolicitudDisponibleResponse] = []
    for i in incidentes:
        score, distancia = calcular_score(
            taller.latitud, taller.longitud, taller.rating or 0.0,
            taller.disponible, i.latitud, i.longitud, i.prioridad,
        )
        eta_estimado_min = None
        if distancia is not None:
            eta_estimado_min = max(5, math.ceil(distancia / 30 * 60))  # 30 km/h urbano, mínimo 5 min
        tipo_problema = i.tipo_incidente or ""
        if not tipo_problema and i.descripcion:
            tipo_problema = clasificador.clasificar(i.descripcion).get("etiqueta_es", "")

        resultado.append(SolicitudDisponibleResponse(
            incidente_id=i.id,
            latitud=float(i.latitud) if i.latitud is not None else None,
            longitud=float(i.longitud) if i.longitud is not None else None,
            descripcion=i.descripcion,
            tipo_problema=tipo_problema,
            prioridad=i.prioridad,
            estado=i.estado,
            fotos_urls=fotos_map.get(i.id, []),
            tiene_audio=audio_map.get(i.id, False),
            created_at=i.created_at.isoformat() if i.created_at else "",
            es_sos=(
                i.prioridad == "alta"
                and i.descripcion is not None
                and "SOS" in (i.descripcion or "")
            ),
            distancia_km=distancia,
            eta_estimado_min=eta_estimado_min,
            score_ia=score,
        ))

    if taller.latitud and taller.longitud:
        resultado.sort(key=lambda x: -x.score_ia)

    return resultado


# ── CU15 – Aceptar solicitud ──────────────────────────────────────────────
@router.patch("/{incidente_id}/aceptar", response_model=AsignacionResponse)
async def aceptar(
    incidente_id: int,
    body: AceptarPayload,
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    taller = await get_taller_by_user(current_user.id, db)

    tiene_asig = (
        exists()
        .where(
            and_(
                Asignacion.incidente_id == incidente_id,
                Asignacion.estado.notin_(_ESTADOS_CERRADOS),
            )
        )
    )
    row = await db.execute(
        select(Incidente, tiene_asig.correlate(None))
        .where(Incidente.id == incidente_id)
    )
    fila = row.first()

    if fila is None:
        raise HTTPException(status_code=404, detail="Incidente no encontrado")

    incidente, ya_asignado = fila
    if incidente.estado != "pendiente":
        raise HTTPException(status_code=400, detail="El incidente ya no está disponible")
    if ya_asignado:
        raise HTTPException(status_code=400, detail="El incidente ya tiene un taller asignado")

    # §4.6 – ETA automático si no se provee y hay coordenadas en ambos extremos
    eta_final = body.eta
    if eta_final is None and taller.latitud and taller.longitud and incidente.latitud and incidente.longitud:
        dist_km = haversine(taller.latitud, taller.longitud, incidente.latitud, incidente.longitud)
        eta_final = max(5, math.ceil(dist_km / 30 * 60))  # 30 km/h urbano, mínimo 5 min

    ahora = datetime.now(timezone.utc)
    asignacion = Asignacion(
        incidente_id=incidente_id,
        taller_id=taller.id,
        eta=eta_final,
        estado="aceptado",
    )
    db.add(asignacion)
    incidente.estado = "en_proceso"
    await db.flush()
    await db.commit()

    response = AsignacionResponse(
        id=asignacion.id,
        incidente_id=asignacion.incidente_id,
        taller_id=asignacion.taller_id,
        tecnico_id=asignacion.tecnico_id,
        estado=asignacion.estado,
        eta=asignacion.eta,
        observacion=asignacion.observacion,
        created_at=ahora,
    )

    try:
        from app.notificaciones.service import notificar_usuario
        eta_txt = f" · ETA {eta_final} min" if eta_final else ""
        await notificar_usuario(
            incidente.usuario_id,
            "✅ Solicitud aceptada",
            f"Un taller aceptó tu solicitud #{incidente_id}{eta_txt}",
            db,
            {"tipo": "solicitud_aceptada", "incidente_id": str(incidente_id)},
        )
    except Exception:
        pass

    return response


# ── CU14 – Ver detalle del incidente ─────────────────────────────────────
@router.get("/{solicitud_id}")
async def detalle(
    solicitud_id: int,
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Incidente)
        .options(undefer(Incidente.tipo_incidente))
        .where(Incidente.id == solicitud_id)
    )
    incidente = result.scalar_one_or_none()
    if not incidente:
        raise HTTPException(status_code=404, detail="Incidente no encontrado")
    return {
        "id":             incidente.id,
        "latitud":        incidente.latitud,
        "longitud":       incidente.longitud,
        "descripcion":    incidente.descripcion,
        "tipo_incidente": incidente.tipo_incidente,
        "estado":         incidente.estado,
        "prioridad":      incidente.prioridad,
        "created_at":     incidente.created_at.isoformat() if incidente.created_at else None,
    }


# ── CU10 – Ver estado de solicitud ───────────────────────────────────────
@router.get("/{solicitud_id}/estado")
async def ver_estado(solicitud_id: int):
    return {"msg": f"CU10 - estado solicitud {solicitud_id}"}


# ── CU11 – Cancelar solicitud (cliente) ──────────────────────────────────
@router.patch("/{solicitud_id}/cancelar")
async def cancelar(
    solicitud_id: int,
    current_user: User = Depends(require_role("cliente")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Incidente).where(
            Incidente.id == solicitud_id,
            Incidente.usuario_id == current_user.id,
        )
    )
    incidente = result.scalar_one_or_none()
    if not incidente:
        raise HTTPException(status_code=404, detail="Incidente no encontrado o no te pertenece")
    if incidente.estado in ("resuelto", "cancelado"):
        raise HTTPException(status_code=400, detail=f"El incidente ya está {incidente.estado}")

    incidente.estado = "cancelado"

    # Cancelar asignaciones activas
    asig_res = await db.execute(
        select(Asignacion).where(
            Asignacion.incidente_id == solicitud_id,
            Asignacion.estado.notin_(_ESTADOS_CERRADOS),
        )
    )
    for asig in asig_res.scalars().all():
        asig.estado = "cancelado"

    await db.commit()

    # Notificar al taller que el cliente canceló
    try:
        from app.notificaciones.service import notificar_usuario
        from app.acceso_registro.models import Taller as _Taller
        for asig in asig_res.scalars():
            t_res = await db.execute(select(_Taller.usuario_id).where(_Taller.id == asig.taller_id))
            t_row = t_res.first()
            if t_row:
                await notificar_usuario(
                    t_row[0],
                    "❌ Solicitud cancelada",
                    f"El cliente canceló el incidente #{solicitud_id}",
                    db,
                    {"tipo": "solicitud_cancelada", "incidente_id": str(solicitud_id)},
                )
    except Exception:
        pass

    return {"id": incidente.id, "estado": incidente.estado, "msg": "Solicitud cancelada correctamente"}


# ── CU16 – Rechazar asignación (taller) ──────────────────────────────────
@router.patch("/{solicitud_id}/rechazar")
async def rechazar(
    solicitud_id: int,
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    taller = await get_taller_by_user(current_user.id, db)

    result = await db.execute(
        select(Asignacion).where(
            Asignacion.incidente_id == solicitud_id,
            Asignacion.taller_id == taller.id,
            Asignacion.estado.notin_(_ESTADOS_CERRADOS),
        )
    )
    asignacion = result.scalar_one_or_none()
    if not asignacion:
        raise HTTPException(status_code=404, detail="No tienes una asignación activa para este incidente")

    if asignacion.estado != "aceptado":
        raise HTTPException(
            status_code=400,
            detail=f"Solo puedes rechazar en estado 'aceptado', estado actual: '{asignacion.estado}'",
        )

    asignacion.estado = "cancelado"

    # Devolver incidente a pendiente para que otro taller pueda aceptarlo
    inc_res = await db.execute(select(Incidente).where(Incidente.id == solicitud_id))
    incidente = inc_res.scalar_one_or_none()
    if incidente and incidente.estado == "en_proceso":
        incidente.estado = "pendiente"

    await db.commit()

    # Notificar al cliente que el taller rechazó
    try:
        from app.notificaciones.service import notificar_usuario
        if incidente:
            await notificar_usuario(
                incidente.usuario_id,
                "🔄 Taller rechazó la solicitud",
                f"El taller rechazó el incidente #{solicitud_id}. Buscando otro taller...",
                db,
                {"tipo": "solicitud_rechazada", "incidente_id": str(solicitud_id)},
            )
    except Exception:
        pass

    return {
        "asignacion_id": asignacion.id,
        "incidente_id":  solicitud_id,
        "estado":        "cancelado",
        "msg":           "Asignación rechazada. El incidente vuelve a estar disponible para otros talleres.",
    }
