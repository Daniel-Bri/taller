"""
Carga datos iniciales en la base de datos.
Uso: python seed.py
"""
import asyncio
import json
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from dotenv import load_dotenv
import os

load_dotenv()

from app.acceso_registro.models import User, Vehiculo, Taller
from app.emergencias.models import Incidente
from app.talleres_tecnicos.models import Tecnico, Asignacion, ServicioRealizado
from app.cotizacion_pagos.models import Cotizacion
from app.db.base import Base
from app.core.security import hash_password

DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    print("[seed] DATABASE_URL no configurada — omitiendo seed.")
    raise SystemExit(0)

_use_ssl = "sslmode=require" in DATABASE_URL or "sslmode=verify-full" in DATABASE_URL

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# asyncpg no entiende sslmode en la URL — quitar del query string
if "sslmode=" in DATABASE_URL:
    from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
    _parsed = urlparse(DATABASE_URL)
    _params = {k: v[0] for k, v in parse_qs(_parsed.query).items() if k != "sslmode"}
    DATABASE_URL = urlunparse(_parsed._replace(query=urlencode(_params)))

_connect_args: dict = {"timeout": 5}
if _use_ssl:
    _connect_args["ssl"] = "require"

engine = create_async_engine(DATABASE_URL, echo=False, connect_args=_connect_args)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# ── Usuarios ─────────────────────────────────────────────────────────────────
USUARIOS = [
    {"email": "admin@taller.com",    "username": "admin",    "full_name": "Administrador",     "password": "12345678", "role": "admin"},
    {"email": "cliente@taller.com",  "username": "cliente",  "full_name": "Carlos Mendoza",    "password": "12345678", "role": "cliente"},
    {"email": "cliente2@taller.com", "username": "cliente2", "full_name": "Ana Quispe",        "password": "12345678", "role": "cliente"},
    {"email": "taller@taller.com",   "username": "taller",   "full_name": "AutoFix Express",   "password": "12345678", "role": "taller"},
    {"email": "taller2@taller.com",  "username": "taller2",  "full_name": "Mecánica Central",  "password": "12345678", "role": "taller"},
    {"email": "tecnico@taller.com",  "username": "tecnico",  "full_name": "Luis Vargas",       "password": "12345678", "role": "tecnico"},
    {"email": "tecnico2@taller.com", "username": "tecnico2", "full_name": "Pedro Huanca",      "password": "12345678", "role": "tecnico"},
]

async def seed():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:

        # ── 1. Usuarios ───────────────────────────────────────────────────────
        print("\n[1/7] Usuarios...")
        users: dict[str, User] = {}
        for data in USUARIOS:
            result = await db.execute(select(User).where(User.email == data["email"]))
            u = result.scalar_one_or_none()
            if u:
                print(f"  [skip] {data['email']}")
            else:
                u = User(
                    email=data["email"],
                    username=data["username"],
                    full_name=data["full_name"],
                    hashed_password=hash_password(data["password"]),
                    role=data["role"],
                )
                db.add(u)
                await db.flush()
                print(f"  [ok]   {data['email']}")
            users[data["username"]] = u
        await db.commit()

        # Recargar IDs tras commit
        for key in users:
            await db.refresh(users[key])

        # ── 2. Vehículos ──────────────────────────────────────────────────────
        print("\n[2/7] Vehículos...")
        VEHICULOS = [
            {"usuario": "cliente",  "placa": "ABC-123", "marca": "Toyota",  "modelo": "Corolla",   "anio": 2019, "color": "Blanco"},
            {"usuario": "cliente",  "placa": "DEF-456", "marca": "Honda",   "modelo": "Civic",     "anio": 2021, "color": "Negro"},
            {"usuario": "cliente2", "placa": "GHI-789", "marca": "Hyundai", "modelo": "Tucson",    "anio": 2020, "color": "Gris"},
            {"usuario": "cliente2", "placa": "JKL-012", "marca": "Kia",     "modelo": "Sportage",  "anio": 2022, "color": "Rojo"},
        ]
        vehiculos: dict[str, Vehiculo] = {}
        for v in VEHICULOS:
            result = await db.execute(select(Vehiculo).where(Vehiculo.placa == v["placa"]))
            veh = result.scalar_one_or_none()
            if veh:
                print(f"  [skip] {v['placa']}")
            else:
                veh = Vehiculo(
                    usuario_id=users[v["usuario"]].id,
                    placa=v["placa"],
                    marca=v["marca"],
                    modelo=v["modelo"],
                    anio=v["anio"],
                    color=v["color"],
                )
                db.add(veh)
                await db.flush()
                print(f"  [ok]   {v['placa']} ({v['marca']} {v['modelo']})")
            vehiculos[v["placa"]] = veh
        await db.commit()
        for k in vehiculos:
            await db.refresh(vehiculos[k])

        # ── 3. Talleres ───────────────────────────────────────────────────────
        print("\n[3/7] Talleres...")
        TALLERES = [
            {
                "usuario": "taller",
                "nombre": "AutoFix Express",
                "direccion": "Av. Américas 1245, La Paz",
                "telefono": "78901234",
                "email_comercial": "autofix@taller.com",
                "latitud": -16.5000, "longitud": -68.1500,
                "estado": "aprobado", "disponible": True, "rating": 4.5,
            },
            {
                "usuario": "taller2",
                "nombre": "Mecánica Central",
                "direccion": "Calle Comercio 890, Cochabamba",
                "telefono": "71234567",
                "email_comercial": "mecanica@taller.com",
                "latitud": -17.3895, "longitud": -66.1568,
                "estado": "pendiente", "disponible": False, "rating": 0.0,
            },
        ]
        talleres: dict[str, Taller] = {}
        for t in TALLERES:
            result = await db.execute(select(Taller).where(Taller.usuario_id == users[t["usuario"]].id))
            tal = result.scalar_one_or_none()
            if tal:
                print(f"  [skip] {t['nombre']}")
            else:
                tal = Taller(
                    usuario_id=users[t["usuario"]].id,
                    nombre=t["nombre"],
                    direccion=t["direccion"],
                    telefono=t["telefono"],
                    email_comercial=t["email_comercial"],
                    latitud=t["latitud"],
                    longitud=t["longitud"],
                    estado=t["estado"],
                    disponible=t["disponible"],
                    rating=t["rating"],
                )
                db.add(tal)
                await db.flush()
                print(f"  [ok]   {t['nombre']} ({t['estado']})")
            talleres[t["usuario"]] = tal
        await db.commit()
        for k in talleres:
            await db.refresh(talleres[k])

        # ── 4. Técnicos ───────────────────────────────────────────────────────
        print("\n[4/7] Técnicos...")
        taller_aprobado = talleres["taller"]
        TECNICOS = [
            {"nombre": "Luis Vargas",    "especialidad": "Motor y transmisión",   "telefono": "71111111", "estado": "ocupado",     "usuario": "tecnico"},
            {"nombre": "Pedro Huanca",   "especialidad": "Eléctrica automotriz",  "telefono": "72222222", "estado": "ocupado",     "usuario": "tecnico2"},
            {"nombre": "Jorge Mamani",   "especialidad": "Frenos y suspensión",   "telefono": "73333333", "estado": "disponible",  "usuario": None},
            {"nombre": "Rosa Chávez",    "especialidad": "Carrocería y pintura",  "telefono": "74444444", "estado": "disponible",  "usuario": None},
            {"nombre": "Mario Quispe",   "especialidad": "Diagnóstico OBD",       "telefono": "75555555", "estado": "inactivo",    "usuario": None},
        ]
        tecnicos: list[Tecnico] = []
        for i, t in enumerate(TECNICOS):
            result = await db.execute(
                select(Tecnico).where(
                    Tecnico.taller_id == taller_aprobado.id,
                    Tecnico.nombre == t["nombre"],
                )
            )
            tec = result.scalar_one_or_none()
            if tec:
                print(f"  [skip] {t['nombre']}")
            else:
                usuario_id = users[t["usuario"]].id if t["usuario"] else None
                tec = Tecnico(
                    taller_id=taller_aprobado.id,
                    usuario_id=usuario_id,
                    nombre=t["nombre"],
                    especialidad=t["especialidad"],
                    telefono=t["telefono"],
                    estado=t["estado"],
                    activo=t["estado"] != "inactivo",
                )
                db.add(tec)
                await db.flush()
                print(f"  [ok]   {t['nombre']} ({t['estado']})")
            tecnicos.append(tec)
        await db.commit()
        for tec in tecnicos:
            await db.refresh(tec)

        # ── 5. Incidentes ─────────────────────────────────────────────────────
        print("\n[5/7] Incidentes...")
        INCIDENTES = [
            # Incidente 1 → asignación finalizada (historial CU22)
            {
                "usuario": "cliente", "placa": "ABC-123",
                "lat": -16.5050, "lon": -68.1480,
                "descripcion": "Vehículo no enciende, batería descargada",
                "estado": "resuelto", "prioridad": "alta",
            },
            # Incidente 2 → asignación finalizada (historial CU22)
            {
                "usuario": "cliente2", "placa": "GHI-789",
                "lat": -16.5100, "lon": -68.1450,
                "descripcion": "Pinchazo de llanta delantera derecha",
                "estado": "resuelto", "prioridad": "media",
            },
            # Incidente 3 → en_reparacion (listo para CU22)
            {
                "usuario": "cliente", "placa": "DEF-456",
                "lat": -16.5200, "lon": -68.1520,
                "descripcion": "Fuga de aceite por el carter, humo blanco",
                "estado": "en_proceso", "prioridad": "alta",
            },
            # Incidente 4 → en_reparacion (listo para CU22)
            {
                "usuario": "cliente2", "placa": "JKL-012",
                "lat": -16.5300, "lon": -68.1400,
                "descripcion": "Frenos no responden correctamente al frenar",
                "estado": "en_proceso", "prioridad": "alta",
            },
            # Incidente 5 → en_camino (activo CU15)
            {
                "usuario": "cliente", "placa": "ABC-123",
                "lat": -16.5150, "lon": -68.1490,
                "descripcion": "Recalentamiento del motor, temperatura muy alta",
                "estado": "en_proceso", "prioridad": "alta",
            },
            # Incidente 6 → aceptado sin técnico (pendiente CU25)
            {
                "usuario": "cliente2", "placa": "GHI-789",
                "lat": -16.5400, "lon": -68.1350,
                "descripcion": "Ruido extraño al acelerar, posible problema en transmisión",
                "estado": "en_proceso", "prioridad": "media",
            },
            # Incidente 7 → pendiente (sin asignación aún)
            {
                "usuario": "cliente", "placa": "DEF-456",
                "lat": -16.5250, "lon": -68.1380,
                "descripcion": "Luces del tablero parpadeando, posible falla eléctrica",
                "estado": "pendiente", "prioridad": "baja",
            },
            # Incidente 8 → para cotización pendiente (CU20)
            {
                "usuario": "cliente2", "placa": "JKL-012",
                "lat": -16.5050, "lon": -68.1600,
                "descripcion": "Cambio de aceite y revisión general preventiva",
                "estado": "en_proceso", "prioridad": "baja",
            },
        ]
        incidentes: list[Incidente] = []
        for inc_data in INCIDENTES:
            inc = Incidente(
                usuario_id=users[inc_data["usuario"]].id,
                vehiculo_id=vehiculos[inc_data["placa"]].id,
                latitud=inc_data["lat"],
                longitud=inc_data["lon"],
                descripcion=inc_data["descripcion"],
                estado=inc_data["estado"],
                prioridad=inc_data["prioridad"],
            )
            db.add(inc)
            await db.flush()
            incidentes.append(inc)
            print(f"  [ok]   Incidente #{inc.id} ({inc_data['prioridad']}) - {inc_data['descripcion'][:50]}...")
        await db.commit()
        for inc in incidentes:
            await db.refresh(inc)

        # ── 6. Asignaciones ───────────────────────────────────────────────────
        print("\n[6/7] Asignaciones...")
        # tecnicos[0]=Luis(ocupado), tecnicos[1]=Pedro(ocupado), tecnicos[2]=Jorge(disponible)
        tec_luis  = tecnicos[0]
        tec_pedro = tecnicos[1]

        ASIGNACIONES = [
            # finalizada → tendrá servicio realizado
            {"incidente": incidentes[0], "tecnico": tec_luis,  "estado": "finalizado",    "eta": None, "obs": "Servicio completado exitosamente"},
            # finalizada → tendrá servicio realizado
            {"incidente": incidentes[1], "tecnico": tec_pedro, "estado": "finalizado",    "eta": None, "obs": "Llanta cambiada sin inconvenientes"},
            # en_reparacion → lista para CU22
            {"incidente": incidentes[2], "tecnico": tec_luis,  "estado": "en_reparacion", "eta": 30,   "obs": "Diagnóstico completado, en proceso de reparación"},
            # en_reparacion → lista para CU22
            {"incidente": incidentes[3], "tecnico": tec_pedro, "estado": "en_reparacion", "eta": 45,   "obs": "Revisando sistema de frenos"},
            # en_camino → activa CU15
            {"incidente": incidentes[4], "tecnico": tec_luis,  "estado": "en_camino",     "eta": 15,   "obs": "Técnico en camino al lugar"},
            # aceptado sin técnico → pendiente CU25
            {"incidente": incidentes[5], "tecnico": None,       "estado": "aceptado",      "eta": None, "obs": None},
            # aceptado con técnico → activa CU15
            {"incidente": incidentes[6], "tecnico": tec_pedro, "estado": "en_sitio",      "eta": 0,    "obs": "Técnico en el lugar evaluando"},
            # aceptado → para cotización CU20
            {"incidente": incidentes[7], "tecnico": None,       "estado": "aceptado",      "eta": None, "obs": None},
        ]
        asignaciones: list[Asignacion] = []
        for a in ASIGNACIONES:
            asig = Asignacion(
                incidente_id=a["incidente"].id,
                taller_id=taller_aprobado.id,
                tecnico_id=a["tecnico"].id if a["tecnico"] else None,
                estado=a["estado"],
                eta=a["eta"],
                observacion=a["obs"],
            )
            db.add(asig)
            await db.flush()
            asignaciones.append(asig)
            tec_nombre = a["tecnico"].nombre if a["tecnico"] else "Sin técnico"
            print(f"  [ok]   Asignación #{asig.id} ({a['estado']}) → {tec_nombre}")
        await db.commit()
        for asig in asignaciones:
            await db.refresh(asig)

        # ── 7a. Servicios Realizados (historial CU22) ─────────────────────────
        print("\n[7/7] Servicios realizados y cotizaciones...")
        SERVICIOS = [
            {
                "asignacion": asignaciones[0],  # finalizada
                "descripcion": "Se realizó carga completa de batería y revisión del sistema eléctrico. Se verificó alternador y cableado.",
                "repuestos": json.dumps([
                    {"descripcion": "Batería 12V 60Ah", "cantidad": 1},
                    {"descripcion": "Terminales de batería", "cantidad": 2},
                ]),
                "observaciones": "Se recomienda revisión eléctrica completa en 6 meses.",
            },
            {
                "asignacion": asignaciones[1],  # finalizada
                "descripcion": "Cambio de llanta delantera derecha por pinchazo. Se revisaron las demás llantas y se ajustó presión.",
                "repuestos": json.dumps([
                    {"descripcion": "Llanta 195/65 R15", "cantidad": 1},
                    {"descripcion": "Parche vulcanizado", "cantidad": 1},
                ]),
                "observaciones": "Las llantas traseras presentan desgaste irregular, considerar alineación.",
            },
        ]
        for s in SERVICIOS:
            result = await db.execute(
                select(ServicioRealizado).where(ServicioRealizado.asignacion_id == s["asignacion"].id)
            )
            srv = result.scalar_one_or_none()
            if srv:
                print(f"  [skip] ServicioRealizado para asignación #{s['asignacion'].id}")
            else:
                srv = ServicioRealizado(
                    asignacion_id=s["asignacion"].id,
                    descripcion_trabajo=s["descripcion"],
                    repuestos=s["repuestos"],
                    observaciones=s["observaciones"],
                )
                db.add(srv)
                await db.flush()
                print(f"  [ok]   ServicioRealizado #{srv.id} para asignación #{s['asignacion'].id}")
        await db.commit()

        # ── 7b. Cotizaciones (CU20) ───────────────────────────────────────────
        COTIZACIONES = [
            {
                "incidente": incidentes[2],   # en_reparacion → cotización aceptada
                "items": [
                    {"descripcion": "Junta del carter",       "cantidad": 1, "precio_unitario": 85.0},
                    {"descripcion": "Aceite de motor 5W-30",  "cantidad": 4, "precio_unitario": 45.0},
                    {"descripcion": "Mano de obra reparación","cantidad": 1, "precio_unitario": 150.0},
                ],
                "estado": "aceptada",
            },
            {
                "incidente": incidentes[3],   # en_reparacion → cotización pendiente
                "items": [
                    {"descripcion": "Pastillas de freno delanteras", "cantidad": 1, "precio_unitario": 120.0},
                    {"descripcion": "Disco de freno",                "cantidad": 2, "precio_unitario": 200.0},
                    {"descripcion": "Líquido de frenos DOT4",        "cantidad": 1, "precio_unitario": 35.0},
                    {"descripcion": "Mano de obra",                  "cantidad": 1, "precio_unitario": 180.0},
                ],
                "estado": "pendiente",
            },
            {
                "incidente": incidentes[7],   # aceptado → cotización pendiente para demo CU20
                "items": [
                    {"descripcion": "Aceite sintético 5W-40",  "cantidad": 4, "precio_unitario": 55.0},
                    {"descripcion": "Filtro de aceite",        "cantidad": 1, "precio_unitario": 30.0},
                    {"descripcion": "Filtro de aire",          "cantidad": 1, "precio_unitario": 40.0},
                    {"descripcion": "Revisión general",        "cantidad": 1, "precio_unitario": 100.0},
                ],
                "estado": "pendiente",
            },
        ]
        for c in COTIZACIONES:
            result = await db.execute(
                select(Cotizacion).where(
                    Cotizacion.incidente_id == c["incidente"].id,
                    Cotizacion.taller_id == taller_aprobado.id,
                )
            )
            cot = result.scalar_one_or_none()
            if cot:
                print(f"  [skip] Cotización para incidente #{c['incidente'].id}")
            else:
                monto = sum(i["cantidad"] * i["precio_unitario"] for i in c["items"])
                cot = Cotizacion(
                    incidente_id=c["incidente"].id,
                    taller_id=taller_aprobado.id,
                    monto_estimado=monto,
                    detalle=json.dumps(c["items"]),
                    estado=c["estado"],
                )
                db.add(cot)
                await db.flush()
                print(f"  [ok]   Cotización #{cot.id} Bs.{monto:.2f} ({c['estado']}) para incidente #{c['incidente'].id}")
        await db.commit()

    print("""
╔══════════════════════════════════════════════════════════════╗
║                    SEED COMPLETADO                          ║
╠══════════════════════════════════════════════════════════════╣
║  CREDENCIALES (password: 12345678 para todos)               ║
║  admin@taller.com    → admin                                ║
║  cliente@taller.com  → cliente (Carlos Mendoza)             ║
║  cliente2@taller.com → cliente (Ana Quispe)                 ║
║  taller@taller.com   → taller  (AutoFix Express - aprobado) ║
║  taller2@taller.com  → taller  (Mecánica Central - pend.)   ║
║  tecnico@taller.com  → tecnico (Luis Vargas)                ║
║  tecnico2@taller.com → tecnico (Pedro Huanca)               ║
╠══════════════════════════════════════════════════════════════╣
║  DATOS CREADOS                                              ║
║  4 Vehículos  │  5 Técnicos  │  8 Incidentes               ║
║  8 Asignaciones:                                            ║
║    • 2 finalizadas  (historial CU22)                        ║
║    • 2 en_reparacion (listas para CU22)                     ║
║    • 1 en_camino    (activa CU15)                           ║
║    • 1 en_sitio     (activa CU15)                           ║
║    • 1 aceptada sin técnico (pendiente CU25)                ║
║    • 1 aceptada sin técnico (para cotización CU20)          ║
║  2 ServiciosRealizados (historial)                          ║
║  3 Cotizaciones: 1 aceptada, 2 pendientes (CU20)            ║
╚══════════════════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    try:
        asyncio.run(seed())
    except Exception as exc:
        print(f"[seed] Error al ejecutar seed (continuando): {exc}")
