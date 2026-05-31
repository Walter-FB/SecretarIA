from models import Cliente, Mensaje, Empresa, Profesional


class EmpresaScope:
    """
    Guardarraíl multi-tenant. Todos los queries pasan por acá,
    así es imposible olvidar el filtro por empresa.
    """

    def __init__(self, session, empresa):
        self.session    = session
        self.empresa    = empresa
        self.empresa_id = empresa.id

    # ── Queries de instancia ─────────────────────────────────────────

    def clientes(self):
        return self.session.query(Cliente).filter(
            Cliente.empresa_id == self.empresa_id
        )

    def cliente_por_telefono(self, telefono: str):
        return self.clientes().filter(Cliente.telefono == telefono).first()

    def mensajes_de_cliente(self, cliente_id: str):
        return self.session.query(Mensaje).filter(
            Mensaje.empresa_id == self.empresa_id,
            Mensaje.cliente_id == cliente_id
        )

    def profesionales(self):
        return self.session.query(Profesional).filter(
            Profesional.empresa_id == self.empresa_id,
            Profesional.activo == True
        )

    def profesional_por_nombre(self, nombre: str):
        nombre_lower = nombre.strip().lower()
        for p in self.profesionales().all():
            if nombre_lower in p.nombre.lower():
                return p
        return None

    def profesional_por_especialidad(self, especialidad: str):
        from services.profesionales import _normalizar_especialidad
        esp = _normalizar_especialidad(especialidad)
        return self.profesionales().filter(Profesional.especialidad == esp).first()

    # ── Lookup estático ─────────────────────────────────────────────

    @staticmethod
    def empresa_por_phone_number_id(phone_number_id: str, session):
        """
        Identifica qué empresa recibe el mensaje entrante.
        Se llama al inicio del webhook con el phone_number_id del payload de Meta.
        Si no hay match, devuelve None (el caller decide si caer al default o ignorar).
        """
        if not phone_number_id:
            return None
        return session.query(Empresa).filter(
            Empresa.phone_number_id == phone_number_id
        ).first()
