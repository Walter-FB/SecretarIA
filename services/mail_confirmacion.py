import os
import logging
import resend

ESPECIALIDAD_DISPLAY = {
    "psiquiatra": "Psiquiatría — Dr. Barros",
    "psicologo":  "Psicología — Lic. Renals",
}


def _build_html(
    nombre: str,
    especialidad: str,
    detalle_turno: str,
    obra_social: str,
    dni: str,
    numero_afiliado: str,
    fecha_nacimiento: str,
    monto: int = None,
    descuento: int = None,
    precio_lista: int = None,
) -> str:
    esp_display = ESPECIALIDAD_DISPLAY.get(
        (especialidad or "").lower(), especialidad or "Consulta"
    )

    obra_row = (
        f'<tr><td class="lbl">Cobertura</td>'
        f'<td>{obra_social}{"  —  Afil: " + numero_afiliado if numero_afiliado else ""}</td></tr>'
        if obra_social and obra_social.lower() != "particular"
        else '<tr><td class="lbl">Cobertura</td><td>Particular</td></tr>'
    )
    dni_row = (
        f'<tr><td class="lbl">DNI</td><td>{dni}</td></tr>' if dni else ""
    )
    nac_row = (
        f'<tr><td class="lbl">Fecha de nac.</td><td>{fecha_nacimiento}</td></tr>'
        if fecha_nacimiento
        else ""
    )

    if monto:
        if descuento and precio_lista:
            os_nombre = (obra_social or "Obra Social") if obra_social and obra_social.lower() != "particular" else "Obra Social"
            pago_rows = f"""
              <tr><td class="lbl">Precio de lista</td><td>${precio_lista:,}</td></tr>
              <tr><td class="lbl">Descuento {os_nombre}</td><td style="color:#2fa8b8">-${descuento:,}</td></tr>
              <tr><td class="lbl" style="font-weight:700;color:#333">Total a pagar</td><td style="font-weight:700;color:#1a6e7e">${monto:,}</td></tr>"""
        else:
            pago_rows = f'<tr><td class="lbl" style="font-weight:700;color:#333">Total a pagar</td><td style="font-weight:700;color:#1a6e7e">${monto:,}</td></tr>'

        pago_section = f"""
    <div class="info-card">
      <div class="ic-title">Detalle del Pago</div>
      <table>{pago_rows}
      </table>
    </div>
    <div class="info-card">
      <div class="ic-title">Datos para Transferir</div>
      <table>
        <tr><td class="lbl">Alias</td><td><strong>juan9910</strong></td></tr>
        <tr><td class="lbl">Titular</td><td>Juan Manuel Barros Ferreyra</td></tr>
        <tr><td class="lbl">CVU</td><td>124235243423432432</td></tr>
      </table>
    </div>"""
    else:
        pago_section = ""

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Confirmación de Turno</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Segoe UI',Arial,sans-serif;background:#eef2f7;padding:30px 16px}}
  .wrap{{max-width:580px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden;
         box-shadow:0 4px 20px rgba(0,0,0,.10)}}
  .hdr{{background:linear-gradient(135deg,#1a6e7e,#2fa8b8);padding:40px 32px 30px;text-align:center;color:#fff}}
  .badge{{width:64px;height:64px;border-radius:50%;background:rgba(255,255,255,.18);
          font-size:32px;line-height:64px;margin:0 auto 14px}}
  .hdr h1{{font-size:24px;font-weight:700;letter-spacing:-.3px;margin-bottom:4px}}
  .hdr p{{font-size:14px;opacity:.85}}
  .body{{padding:32px 32px 24px}}
  .greet{{font-size:16px;color:#333;margin-bottom:22px}}
  .greet b{{color:#1a6e7e}}
  .turno-card{{background:linear-gradient(135deg,#1a6e7e,#2fa8b8);color:#fff;
               border-radius:10px;padding:22px 24px;margin-bottom:20px}}
  .turno-card .tc-label{{font-size:11px;font-weight:700;text-transform:uppercase;
                          letter-spacing:1.2px;opacity:.75;margin-bottom:8px}}
  .turno-card .tc-detail{{font-size:17px;font-weight:700;line-height:1.3}}
  .turno-card .tc-esp{{font-size:13px;opacity:.88;margin-top:5px}}
  .info-card{{background:#f7fbfc;border:1px solid #c8e6ec;border-radius:10px;
              padding:18px 22px;margin-bottom:20px}}
  .info-card .ic-title{{font-size:11px;font-weight:700;text-transform:uppercase;
                         letter-spacing:1px;color:#1a6e7e;margin-bottom:12px}}
  .info-card table{{width:100%;border-collapse:collapse}}
  .info-card td{{padding:6px 0;font-size:13.5px;color:#444;vertical-align:top}}
  .info-card td.lbl{{color:#888;width:44%;font-weight:500}}
  .tip{{font-size:13px;color:#666;background:#fffef0;border-left:3px solid #f5c518;
        padding:12px 16px;border-radius:0 8px 8px 0;margin-bottom:22px;line-height:1.5}}
  .ftr{{background:#f2f2f2;padding:18px 32px;text-align:center;font-size:12px;color:#888}}
  .ftr a{{color:#1a6e7e;text-decoration:none}}
</style>
</head>
<body>
<div class="wrap">

  <div class="hdr">
    <div class="badge">&#10003;</div>
    <h1>Turno Confirmado</h1>
    <p>Cl&iacute;nica Abriness &mdash; Centro de Salud Mental</p>
  </div>

  <div class="body">
    <p class="greet">¡Hola, <b>{nombre}</b>!<br>
    Tu turno fue registrado exitosamente. Acá tenés el resumen completo.</p>

    <div class="turno-card">
      <div class="tc-label">Detalle del turno</div>
      <div class="tc-detail">{detalle_turno}</div>
      <div class="tc-esp">{esp_display}</div>
    </div>

    <div class="info-card">
      <div class="ic-title">Datos del Paciente</div>
      <table>
        <tr><td class="lbl">Nombre</td><td>{nombre}</td></tr>
        {dni_row}
        {nac_row}
        {obra_row}
      </table>
    </div>

    {pago_section}

    <div class="tip">
      &#x1F4CC; Presentate <strong>10 minutos antes</strong> de tu turno.<br>
      Para cancelar o reprogramar, avisanos con anticipaci&oacute;n por WhatsApp.
    </div>
  </div>

  <div class="ftr">
    Cl&iacute;nica Abriness &mdash; Centro de Salud Mental<br>
    ¿Preguntas? <a href="https://wa.me/5491131720843">Escrib&iacute;nos por WhatsApp</a>
  </div>

</div>
</body>
</html>"""


async def enviar_mail_confirmacion(
    mail_destino: str,
    nombre: str,
    especialidad: str,
    detalle_turno: str,
    obra_social: str = None,
    dni: str = None,
    numero_afiliado: str = None,
    fecha_nacimiento: str = None,
    monto: int = None,
    descuento: int = None,
    precio_lista: int = None,
) -> bool:
    """Sends the HTML confirmation email via Resend. Returns True on success."""
    if not mail_destino:
        logging.warning("[📧 MAIL] Sin dirección de email, no se envía.")
        return False

    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        logging.warning("[📧 MAIL] ❌ Falta RESEND_API_KEY en las variables de entorno.")
        return False

    resend.api_key = api_key
    nombre        = nombre or "Paciente"
    detalle_turno = detalle_turno or "Tu próximo turno en Clínica Abriness"

    html = _build_html(nombre, especialidad, detalle_turno, obra_social, dni, numero_afiliado, fecha_nacimiento, monto, descuento, precio_lista)

    logging.warning(f"[📧 RESEND] Enviando a {mail_destino} | nombre={nombre} | especialidad={especialidad}")
    try:
        params = resend.Emails.SendParams(
            from_="Clínica Abriness <onboarding@resend.dev>",
            to=[mail_destino],
            subject="✅ Turno confirmado — Clínica Abriness",
            html=html,
        )
        r = resend.Emails.send(params)
        logging.warning(f"[📧 RESEND] ✅ Mail enviado. ID: {r.get('id')}")
        return True
    except Exception as e:
        logging.warning(f"[📧 RESEND] ❌ Error al enviar a {mail_destino}: {e}")
        import traceback; logging.warning(traceback.format_exc())
        return False
