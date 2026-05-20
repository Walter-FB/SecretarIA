import smtplib
import os
import asyncio
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

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


def _enviar_smtp(to: str, html: str) -> None:
    import logging
    host     = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port     = int(os.getenv("SMTP_PORT", "587"))
    user     = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASS")
    from_addr = os.getenv("SMTP_FROM", user)

    logging.warning(f"[📧 SMTP] Intentando envío a {to} | host={host}:{port} | user={user}")

    if not user or not password:
        logging.warning("[📧 SMTP] ❌ Faltan SMTP_USER o SMTP_PASS en las variables de entorno.")
        raise ValueError("Faltan SMTP_USER o SMTP_PASS en las variables de entorno.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "✅ Turno confirmado — Clínica Abriness"
    msg["From"]    = formataddr(("Clínica Abriness", from_addr))
    msg["To"]      = to
    msg.attach(MIMEText(html, "html", "utf-8"))

    logging.warning(f"[📧 SMTP] Conectando a {host}:{port}...")
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=15) as server:
            server.ehlo()
            logging.warning(f"[📧 SMTP] SSL OK. Haciendo login con {user}...")
            server.login(user, password)
            logging.warning(f"[📧 SMTP] Login OK. Enviando...")
            server.sendmail(from_addr, to, msg.as_string())
            logging.warning(f"[📧 SMTP] ✅ Mail enviado exitosamente a {to}")
    else:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.ehlo()
            server.starttls()
            logging.warning(f"[📧 SMTP] STARTTLS OK. Haciendo login con {user}...")
            server.login(user, password)
            logging.warning(f"[📧 SMTP] Login OK. Enviando...")
            server.sendmail(from_addr, to, msg.as_string())
            logging.warning(f"[📧 SMTP] ✅ Mail enviado exitosamente a {to}")


async def enviar_mail_confirmacion(
    mail_destino: str,
    nombre: str,
    especialidad: str,
    detalle_turno: str,
    obra_social: str = None,
    dni: str = None,
    numero_afiliado: str = None,
    fecha_nacimiento: str = None,
) -> bool:
    """Sends the HTML confirmation email. Returns True on success."""
    if not mail_destino:
        print("[📧 MAIL] Sin dirección de email, no se envía.")
        return False

    nombre         = nombre or "Paciente"
    detalle_turno  = detalle_turno or "Tu próximo turno en Clínica Abriness"

    html = _build_html(nombre, especialidad, detalle_turno, obra_social, dni, numero_afiliado, fecha_nacimiento)

    try:
        await asyncio.to_thread(_enviar_smtp, mail_destino, html)
        logging.warning(f"[📧 MAIL] ✅ Confirmación enviada a {mail_destino}")
        return True
    except Exception as e:
        logging.warning(f"[📧 MAIL] ❌ No se pudo enviar a {mail_destino}: {e}")
        import traceback; logging.warning(traceback.format_exc())
        return False
