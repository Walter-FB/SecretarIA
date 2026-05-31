import os
import secrets
import logging
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from database import SessionLocal
from models import Cliente, Mensaje, Empresa

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
if not ADMIN_TOKEN:
    logging.warning(
        "[⚠️ ADMIN] ADMIN_TOKEN no configurado — "
        "el panel /admin no va a responder. Setealo en Railway."
    )

router = APIRouter(prefix="/admin")


def _require_admin(authorization: str | None = Header(default=None)):
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN no configurado en el servidor")
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header requerido")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Formato inválido. Usar: Authorization: Bearer <token>")
    if not secrets.compare_digest(parts[1], ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Token inválido")


# ── Panel HTML (sin auth — el token se ingresa dentro del panel) ──────
@router.get("", include_in_schema=False)
def admin_panel():
    html_path = Path(__file__).parent.parent / "static" / "admin.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


# ── Datos (todos protegidos) ──────────────────────────────────────────

@router.get("/empresas", dependencies=[Depends(_require_admin)])
def get_empresas():
    db = SessionLocal()
    try:
        empresas = db.query(Empresa).all()
        return [
            {
                "id":               e.id,
                "nombre":           e.nombre,
                "bot_activo":       e.bot_activo,
                "cantidad_clientes": db.query(Cliente).filter(Cliente.empresa_id == e.id).count(),
            }
            for e in empresas
        ]
    finally:
        db.close()


@router.get("/clientes", dependencies=[Depends(_require_admin)])
def get_clientes(empresa_id: str):
    db = SessionLocal()
    try:
        clientes = db.query(Cliente).filter(Cliente.empresa_id == empresa_id).all()
        result = []
        for c in clientes:
            ultimo = (
                db.query(Mensaje)
                .filter(Mensaje.cliente_id == c.id)
                .order_by(Mensaje.fecha_creacion.desc())
                .first()
            )
            result.append({
                "id":               c.id,
                "telefono":         c.telefono,
                "nombre":           c.nombre_completo or "",
                "estado_agente":    c.estado_agente,
                "bot_activo":       c.bot_activo,
                "mensajes_enviados": c.mensajes_enviados,
                "ultimo_mensaje":   ultimo.fecha_creacion.isoformat() if ultimo else None,
            })
        return result
    finally:
        db.close()


@router.get("/conversacion/{telefono}", dependencies=[Depends(_require_admin)])
def get_conversacion(telefono: str, empresa_id: str):
    db = SessionLocal()
    try:
        cliente = db.query(Cliente).filter(
            Cliente.telefono == telefono,
            Cliente.empresa_id == empresa_id,
        ).first()
        if not cliente:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
        mensajes = (
            db.query(Mensaje)
            .filter(Mensaje.cliente_id == cliente.id)
            .order_by(Mensaje.fecha_creacion.asc())
            .all()
        )
        return {
            "telefono":     telefono,
            "nombre":       cliente.nombre_completo or "",
            "estado_agente": cliente.estado_agente,
            "bot_activo":   cliente.bot_activo,
            "obra_social":  cliente.obra_social or "",
            "mensajes": [
                {"rol": m.rol, "texto": m.texto, "fecha": m.fecha_creacion.isoformat()}
                for m in mensajes
            ],
        }
    finally:
        db.close()


class MuteBody(BaseModel):
    telefono:   str
    empresa_id: str


@router.post("/mute", dependencies=[Depends(_require_admin)])
def mute_cliente(body: MuteBody):
    db = SessionLocal()
    try:
        c = db.query(Cliente).filter(
            Cliente.telefono == body.telefono,
            Cliente.empresa_id == body.empresa_id,
        ).first()
        if not c:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
        c.bot_activo = False
        db.commit()
        logging.warning(f"[ADMIN] mute → {body.telefono}")
        return {"ok": True, "bot_activo": False}
    finally:
        db.close()


@router.post("/unmute", dependencies=[Depends(_require_admin)])
def unmute_cliente(body: MuteBody):
    db = SessionLocal()
    try:
        c = db.query(Cliente).filter(
            Cliente.telefono == body.telefono,
            Cliente.empresa_id == body.empresa_id,
        ).first()
        if not c:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
        c.bot_activo = True
        db.commit()
        logging.warning(f"[ADMIN] unmute → {body.telefono}")
        return {"ok": True, "bot_activo": True}
    finally:
        db.close()
