"""
Helpers de acceso a la tabla `profesionales`.
Sin lógica de negocio — solo lectura/búsqueda.
"""
import os
import logging
from models import Profesional


def get_profesionales_activos(db) -> list[Profesional]:
    return db.query(Profesional).filter(Profesional.activo == True).all()


def get_profesional_by_id(db, profesional_id: str) -> Profesional | None:
    return db.query(Profesional).filter(Profesional.id == profesional_id).first()


def get_profesional_by_nombre(db, nombre: str) -> Profesional | None:
    """Búsqueda case-insensitive por nombre parcial. Ej: 'renals' matchea 'Lic. Renals'."""
    nombre_lower = nombre.strip().lower()
    todos = db.query(Profesional).filter(Profesional.activo == True).all()
    for p in todos:
        if nombre_lower in p.nombre.lower():
            return p
    return None


def get_profesional_by_especialidad(db, especialidad: str) -> Profesional | None:
    """Devuelve el primer profesional activo de la especialidad indicada."""
    esp = _normalizar_especialidad(especialidad)
    return (
        db.query(Profesional)
        .filter(Profesional.especialidad == esp, Profesional.activo == True)
        .first()
    )


def get_calendar_id(profesional: Profesional | None) -> str:
    """Devuelve el calendar_id del profesional, o el env var CALENDAR_ID como fallback."""
    if profesional and profesional.calendar_id:
        return profesional.calendar_id
    calendar_id = os.getenv("CALENDAR_ID", "primary")
    logging.warning(f"[📅 CALENDAR] Usando CALENDAR_ID de env: {calendar_id}")
    return calendar_id


def get_tarifa(profesional: Profesional | None, modalidad: str) -> int:
    """
    Devuelve la tarifa según la modalidad ('particular' u 'obra social').
    Si no hay profesional, usa los valores hardcodeados de fallback.
    """
    modalidad_norm = (modalidad or "particular").strip().lower()

    if profesional:
        if "obra" in modalidad_norm or "social" in modalidad_norm:
            return profesional.tarifa_obra_social
        return profesional.tarifa_particular

    # Fallback si por alguna razón no hay profesional en contexto
    logging.warning("[⚠️ TARIFAS] get_tarifa llamado sin profesional — usando fallback hardcodeado")
    FALLBACK = {"psicologo": {"particular": 30000, "obra social": 19000},
                "psiquiatra": {"particular": 80000, "obra social": 45000}}
    return FALLBACK.get("psicologo", {}).get(modalidad_norm, 30000)


def _normalizar_especialidad(texto: str) -> str:
    """Normaliza texto libre a 'psicologo' o 'psiquiatra'."""
    import unicodedata
    if not texto:
        return "psicologo"
    norm = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    if "psiquiatr" in norm or "barros" in norm or "dr." in norm:
        return "psiquiatra"
    return "psicologo"
