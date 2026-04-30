import math
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.dependencies import get_current_user, require_role
from app.acceso_registro.models import User
from app.reportes import schemas, service

router = APIRouter()


# ── CU32 - Recordatorios de mantenimiento ─────────────────
@router.get("/mantenimiento")
async def recordatorios_mantenimiento(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await service.obtener_recordatorios_mantenimiento(current_user.id, db)


# ── CU28 - Calificar servicio ──────────────────────────────
@router.get(
    "/calificaciones/pendientes",
    response_model=list[schemas.CalificacionPendienteItem],
)
async def calificaciones_pendientes(
    current_user: User = Depends(require_role("cliente")),
    db: AsyncSession = Depends(get_db),
):
    rows = await service.listar_calificaciones_pendientes(current_user.id, db)
    return [schemas.CalificacionPendienteItem(**r) for r in rows]


@router.post(
    "/calificaciones",
    response_model=schemas.CalificacionResponse,
)
async def calificar_servicio(
    data: schemas.CalificacionCreate,
    current_user: User = Depends(require_role("cliente")),
    db: AsyncSession = Depends(get_db),
):
    cal = await service.crear_calificacion(
        cliente_id=current_user.id,
        asignacion_id=data.asignacion_id,
        puntuacion=data.puntuacion,
        resena=data.resena,
        db=db,
    )
    return schemas.CalificacionResponse.model_validate(cal)


# ── CU29 - Ver historial de servicios ─────────────────────
@router.get("/historial")
async def historial(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.talleres_tecnicos.models import ServicioRealizado, Asignacion
    from app.cotizacion_pagos.models import Cotizacion
    from app.emergencias.models import Incidente

    if current_user.role in ("taller", "tecnico"):
        from app.talleres_tecnicos.service import listar_servicios_realizados
        servicios = await listar_servicios_realizados(current_user.id, current_user.role, db)
    elif current_user.role == "cliente":
        res = await db.execute(
            select(ServicioRealizado)
            .join(Asignacion, ServicioRealizado.asignacion_id == Asignacion.id)
            .join(Incidente, Asignacion.incidente_id == Incidente.id)
            .where(Incidente.usuario_id == current_user.id)
            .order_by(ServicioRealizado.fecha_cierre.desc())
        )
        servicios = list(res.scalars().all())
    else:
        servicios = []

    if not servicios:
        return []

    asig_ids = [s.asignacion_id for s in servicios]

    # Mapear asignacion → incidente
    asig_res = await db.execute(
        select(Asignacion.id, Asignacion.incidente_id).where(Asignacion.id.in_(asig_ids))
    )
    asig_to_inc: dict[int, int] = {row[0]: row[1] for row in asig_res.all()}
    inc_ids = list(set(asig_to_inc.values()))

    # Info de incidentes (batch)
    inc_res = await db.execute(
        select(Incidente.id, Incidente.descripcion, Incidente.tipo_incidente)
        .where(Incidente.id.in_(inc_ids))
    )
    inc_data: dict[int, dict] = {
        row[0]: {"incidente_id": row[0], "descripcion": row[1], "tipo_incidente": row[2]}
        for row in inc_res.all()
    }

    # Cotizaciones aceptadas/pagadas (batch)
    cot_res = await db.execute(
        select(Cotizacion.incidente_id, Cotizacion.monto_estimado, Cotizacion.estado)
        .where(
            Cotizacion.incidente_id.in_(inc_ids),
            Cotizacion.estado.in_(["aceptada", "pagada"]),
        )
    )
    cot_data: dict[int, float] = {row[0]: row[1] for row in cot_res.all()}

    return [
        {
            "id":                  s.id,
            "asignacion_id":       s.asignacion_id,
            "descripcion_trabajo": s.descripcion_trabajo,
            "repuestos":           s.repuestos,
            "observaciones":       s.observaciones,
            "fecha_cierre":        s.fecha_cierre.isoformat() if s.fecha_cierre else None,
            "monto_cotizacion":    cot_data.get(asig_to_inc.get(s.asignacion_id)),
            **(inc_data.get(asig_to_inc.get(s.asignacion_id, 0), {})),
        }
        for s in servicios
    ]


# ── Métricas del taller ───────────────────────────────────
@router.get("/metricas/taller")
async def metricas_taller(
    desde: Optional[datetime] = Query(None),
    hasta: Optional[datetime] = Query(None),
    current_user: User = Depends(require_role("taller")),
    db: AsyncSession = Depends(get_db),
):
    from app.talleres_tecnicos.service import get_taller_by_user

    taller = await get_taller_by_user(current_user.id, db)
    resumen = await service.obtener_metricas(db, desde, hasta, taller_id=taller.id)
    return schemas.MetricasResumenResponse(**resumen)


# ── Métricas globales ─────────────────────────────────────
@router.get("/metricas/globales")
async def metricas_globales(
    desde: Optional[datetime] = Query(None),
    hasta: Optional[datetime] = Query(None),
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    resumen = await service.obtener_metricas(db, desde, hasta, taller_id=None)
    return schemas.MetricasResumenResponse(**resumen)


# ── CU35 - Auditoría / Bitácora ────────────────────────────
@router.get("/auditoria", response_model=schemas.AuditoriaListResponse)
async def listar_auditoria(
    desde: Optional[datetime] = Query(None),
    hasta: Optional[datetime] = Query(None),
    accion: Optional[str] = Query(None),
    usuario_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    items, total = await service.listar_eventos(db, desde, hasta, accion, usuario_id, page, size)
    pages = math.ceil(total / size) if total > 0 else 1
    return schemas.AuditoriaListResponse(
        items=[schemas.BitacoraEventoResponse.model_validate(e) for e in items],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


@router.get("/auditoria/exportar")
async def exportar_auditoria(
    desde: Optional[datetime] = Query(None),
    hasta: Optional[datetime] = Query(None),
    accion: Optional[str] = Query(None),
    usuario_id: Optional[int] = Query(None),
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    csv_content = await service.exportar_csv(db, desde, hasta, accion, usuario_id)
    headers = {
        "Content-Disposition": "attachment; filename=auditoria.csv",
        "Content-Type": "text/csv; charset=utf-8",
    }
    return Response(content=csv_content.encode("utf-8"), headers=headers)


@router.get("/auditoria/{evento_id}", response_model=schemas.BitacoraEventoResponse)
async def detalle_evento(
    evento_id: int,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    evento = await service.obtener_evento(evento_id, db)
    if not evento:
        raise HTTPException(status_code=404, detail="Evento no encontrado")
    return schemas.BitacoraEventoResponse.model_validate(evento)
