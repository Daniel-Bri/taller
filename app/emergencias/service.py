import json
import logging
import os
import time

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.emergencias.models import Evidencia, Incidente
from app.emergencias.schemas import IncidenteCreate, UbicacionUpdate
from app.acceso_registro.models import Taller, Vehiculo
from app.talleres_tecnicos.models import Asignacion
from app.ia import clasificador

logger = logging.getLogger(__name__)

_UPLOAD_DIR = "uploads"
_MAPEO_IMAGEN_A_TIPO = {
    "motor_humo": "sobrecalentamiento",
    "llanta_dano": "llanta_ponchada",
    "dano_carroceria": "accidente",
    "vidrio_roto": "accidente",
    "multiple_dano": "accidente",
}


# ── helpers ─────────────────────────────────────────────────────────────────

async def _refetch(incidente_id: int, db: AsyncSession) -> "Incidente":
    """Re-fetch with tipo_incidente undeferred to avoid MissingGreenlet on lazy load."""
    result = await db.execute(
        select(Incidente)
        .options(undefer(Incidente.tipo_incidente))
        .where(Incidente.id == incidente_id)
    )
    return result.scalar_one()


async def _get_incidente_usuario(
    incidente_id: int, usuario_id: int, db: AsyncSession
) -> Incidente:
    result = await db.execute(
        select(Incidente)
        .options(undefer(Incidente.tipo_incidente))
        .where(Incidente.id == incidente_id, Incidente.usuario_id == usuario_id)
    )
    inc = result.scalar_one_or_none()
    if not inc:
        raise HTTPException(status_code=404, detail="Incidente no encontrado")
    return inc


# ── CU05 ─────────────────────────────────────────────────────────────────────

async def crear_incidente(
    data: IncidenteCreate, usuario_id: int, db: AsyncSession
) -> Incidente:
    result = await db.execute(
        select(Vehiculo).where(
            Vehiculo.id == data.vehiculo_id,
            Vehiculo.usuario_id == usuario_id,
            Vehiculo.activo.is_(True),
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Vehículo no encontrado o no pertenece al usuario")

    # §4.5 – Clasificación automática con IA
    tipo_incidente = None
    if data.descripcion and data.descripcion.strip():
        res_ia = clasificador.clasificar(data.descripcion)
        tipo_incidente = res_ia["tipo"]

    incidente = Incidente(
        usuario_id=usuario_id,
        vehiculo_id=data.vehiculo_id,
        descripcion=data.descripcion,
        prioridad=data.prioridad or "media",
        tipo_incidente=tipo_incidente,
    )
    db.add(incidente)
    await db.commit()
    await db.flush()
    return await _refetch(incidente.id, db)


# ── CU30 ─────────────────────────────────────────────────────────────────────

async def crear_incidente_sos(
    usuario_id: int,
    latitud: float | None,
    longitud: float | None,
    db: AsyncSession,
) -> Incidente:
    veh_res = await db.execute(
        select(Vehiculo)
        .where(Vehiculo.usuario_id == usuario_id, Vehiculo.activo.is_(True))
        .order_by(Vehiculo.created_at.asc())
    )
    vehiculo = veh_res.scalars().first()
    if not vehiculo:
        raise HTTPException(
            status_code=400,
            detail="Debes tener al menos un vehículo registrado para usar el botón SOS",
        )

    incidente = Incidente(
        usuario_id=usuario_id,
        vehiculo_id=vehiculo.id,
        descripcion="🆘 Alerta SOS — Emergencia urgente enviada desde la app",
        prioridad="alta",
        latitud=latitud,
        longitud=longitud,
        tipo_incidente="otros",
    )
    db.add(incidente)
    await db.commit()
    await db.flush()
    return await _refetch(incidente.id, db)


# ── CU06 ─────────────────────────────────────────────────────────────────────

async def actualizar_ubicacion(
    incidente_id: int, usuario_id: int, data: UbicacionUpdate, db: AsyncSession
) -> Incidente:
    incidente = await _get_incidente_usuario(incidente_id, usuario_id, db)
    incidente.latitud  = data.latitud
    incidente.longitud = data.longitud
    await db.commit()
    return await _refetch(incidente.id, db)


# ── CU09 ─────────────────────────────────────────────────────────────────────

async def actualizar_descripcion(
    incidente_id: int, usuario_id: int, descripcion: str, db: AsyncSession
) -> Incidente:
    """Actualiza la descripción y re-clasifica el tipo con IA."""
    incidente = await _get_incidente_usuario(incidente_id, usuario_id, db)
    incidente.descripcion = descripcion
    if descripcion.strip():
        res_ia = clasificador.clasificar(descripcion)
        incidente.tipo_incidente = res_ia["tipo"]
    await db.commit()
    return await _refetch(incidente.id, db)


# ── CU07 ─────────────────────────────────────────────────────────────────────

async def guardar_foto(
    incidente_id: int,
    usuario_id: int,
    imagen_bytes: bytes,
    filename: str,
    db: AsyncSession,
) -> dict:
    """Guarda la foto, ejecuta análisis IA (§4.4 + §4.5)."""
    incidente = await _get_incidente_usuario(incidente_id, usuario_id, db)

    from app.ia import analizador_imagen

    ts  = int(time.time() * 1000)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    if ext not in ("jpg", "jpeg", "png", "webp"):
        ext = "jpg"
    ruta_rel = f"fotos/{incidente_id}_{ts}.{ext}"
    ruta_abs = os.path.join(_UPLOAD_DIR, ruta_rel)
    os.makedirs(os.path.dirname(ruta_abs), exist_ok=True)

    with open(ruta_abs, "wb") as fh:
        fh.write(imagen_bytes)

    analisis   = analizador_imagen.analizar(imagen_bytes)
    categoria_img = str(analisis.get("categoria", "") or "")
    confianza_img = float(analisis.get("confianza", 0.0) or 0.0)
    tipo_por_imagen = _MAPEO_IMAGEN_A_TIPO.get(categoria_img)

    # Fusiona texto+imagen: si la imagen es suficientemente confiable, actualiza tipo_incidente.
    # Si ya existe tipo textual, la imagen solo lo pisa con confianza más alta.
    if tipo_por_imagen:
        try:
            confianza_texto = 0.0
            if incidente.descripcion and incidente.descripcion.strip():
                res_texto = clasificador.clasificar(incidente.descripcion)
                confianza_texto = float(res_texto.get("confianza", 0.0) or 0.0)

            if not incidente.tipo_incidente:
                if confianza_img >= 0.55:
                    incidente.tipo_incidente = tipo_por_imagen
            elif confianza_img >= max(0.65, confianza_texto + 0.10):
                incidente.tipo_incidente = tipo_por_imagen
        except Exception:
            logger.exception("No se pudo fusionar clasificación texto+imagen para incidente %s", incidente_id)
    url_publica = f"/uploads/{ruta_rel}"

    evidencia = Evidencia(
        incidente_id=incidente_id,
        tipo="foto",
        ruta=ruta_abs,
        url=url_publica,
        analisis_ia=json.dumps(analisis),
    )
    db.add(evidencia)
    await db.commit()
    await db.refresh(evidencia)

    return {
        "evidencia_id": evidencia.id,
        "url": url_publica,
        "analisis_ia": analisis,
    }


# ── CU08 ─────────────────────────────────────────────────────────────────────

async def guardar_audio(
    incidente_id: int,
    usuario_id: int,
    audio_bytes: bytes,
    filename: str,
    db: AsyncSession,
) -> dict:
    """Guarda el audio, transcribe y clasifica el incidente con IA (§4.5)."""
    await _get_incidente_usuario(incidente_id, usuario_id, db)

    from app.ia import transcriptor, clasificador as clf

    ts  = int(time.time() * 1000)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "wav"
    if ext not in ("wav", "mp3", "ogg", "m4a", "flac"):
        ext = "wav"
    ruta_rel = f"audio/{incidente_id}_{ts}.{ext}"
    ruta_abs = os.path.join(_UPLOAD_DIR, ruta_rel)
    os.makedirs(os.path.dirname(ruta_abs), exist_ok=True)

    with open(ruta_abs, "wb") as fh:
        fh.write(audio_bytes)

    res_transcripcion = transcriptor.transcribir(audio_bytes, ext)
    clasificacion     = None
    texto_transcrito  = res_transcripcion.get("transcripcion", "")

    if res_transcripcion.get("exito") and texto_transcrito:
        clasificacion = clf.clasificar(texto_transcrito)
        # Actualizar tipo_incidente si la confianza es alta y aún no tenía tipo
        if clasificacion and clasificacion.get("confianza", 0) > 0.5:
            inc_upd = await db.execute(
                select(Incidente)
                .options(undefer(Incidente.tipo_incidente))
                .where(Incidente.id == incidente_id)
            )
            inc = inc_upd.scalar_one_or_none()
            if inc and not inc.tipo_incidente:
                inc.tipo_incidente = clasificacion["tipo"]

    url_publica = f"/uploads/{ruta_rel}"
    evidencia = Evidencia(
        incidente_id=incidente_id,
        tipo="audio",
        ruta=ruta_abs,
        url=url_publica,
        transcripcion=texto_transcrito or None,
        analisis_ia=json.dumps(clasificacion) if clasificacion else None,
    )
    db.add(evidencia)
    await db.commit()
    await db.refresh(evidencia)

    return {
        "evidencia_id": evidencia.id,
        "url": url_publica,
        "transcripcion": res_transcripcion,
        "clasificacion": clasificacion,
    }


# ── CU10 ─────────────────────────────────────────────────────────────────────

async def listar_incidentes_usuario(usuario_id: int, db: AsyncSession) -> list[Incidente]:
    result = await db.execute(
        select(Incidente)
        .options(undefer(Incidente.tipo_incidente))
        .where(Incidente.usuario_id == usuario_id)
        .order_by(Incidente.created_at.desc())
    )
    return list(result.scalars().all())


async def obtener_incidente(incidente_id: int, db: AsyncSession) -> Incidente:
    result = await db.execute(
        select(Incidente)
        .options(undefer(Incidente.tipo_incidente))
        .where(Incidente.id == incidente_id)
    )
    inc = result.scalar_one_or_none()
    if not inc:
        raise HTTPException(status_code=404, detail="Incidente no encontrado")
    return inc


async def listar_mis_solicitudes(usuario_id: int, db: AsyncSession) -> list[dict]:
    inc_res = await db.execute(
        select(Incidente)
        .options(undefer(Incidente.tipo_incidente))
        .where(Incidente.usuario_id == usuario_id)
        .order_by(Incidente.created_at.desc())
    )
    incidentes = list(inc_res.scalars().all())

    rows = []
    for inc in incidentes:
        asig_res = await db.execute(
            select(Asignacion).where(Asignacion.incidente_id == inc.id)
        )
        asig = asig_res.scalar_one_or_none()

        asig_data = None
        if asig:
            taller_res = await db.execute(select(Taller).where(Taller.id == asig.taller_id))
            taller     = taller_res.scalar_one_or_none()
            asig_data  = {
                "id": asig.id,
                "estado": asig.estado,
                "eta": asig.eta,
                "taller_id": asig.taller_id,
                "taller_nombre": taller.nombre if taller else None,
                "tecnico_id": asig.tecnico_id,
                "observacion": asig.observacion,
            }

        # Evidencias reales (§4.4)
        evid_res = await db.execute(
            select(Evidencia.tipo, Evidencia.url)
            .where(Evidencia.incidente_id == inc.id)
        )
        evid_rows = evid_res.all()
        fotos_urls = [r.url for r in evid_rows if r.tipo == "foto" and r.url]

        rows.append({
            "incidente": {
                "id":            inc.id,
                "vehiculo_id":   inc.vehiculo_id,
                "estado":        inc.estado,
                "prioridad":     inc.prioridad,
                "tipo_incidente": inc.tipo_incidente,
                "descripcion":   inc.descripcion,
                "latitud":       inc.latitud,
                "longitud":      inc.longitud,
                "created_at":    inc.created_at.isoformat() if inc.created_at else None,
            },
            "asignacion": asig_data,
            "fotos_urls": fotos_urls,
        })
    return rows
