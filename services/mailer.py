"""Envío de correos transaccionales (recuperación de contraseña) con Resend.

Configuración, siempre por variables de entorno (nunca en el código):

- `EMAIL_PROVIDER`: `resend` (envía de verdad), `console` (solo desarrollo: escribe el enlace en el log)
  o vacío/`disabled` (no envía nada; la API responde igual y deja un aviso en el log).
- `RESEND_API_KEY`: clave de la API de Resend (obligatoria con `EMAIL_PROVIDER=resend`).
- `EMAIL_FROM`: remitente; por defecto el de pruebas de Resend (`onboarding@resend.dev`).
- `FRONTEND_BASE_URL`: dirección de la app que sirve `/reset-password` (por defecto el backoffice).
"""

import html
import logging
import os
from dataclasses import dataclass

import httpx

logger = logging.getLogger("services.mailer")

RESEND_URL = "https://api.resend.com/emails"
DEFAULT_FROM = "TrackFlow <onboarding@resend.dev>"
DEFAULT_FRONTEND_BASE_URL = "http://localhost:3001"
PROVIDERS = ("resend", "console", "disabled")
SEND_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class EmailMessage:
    """Un correo listo para enviar, con versión HTML y de texto plano."""

    to: str
    subject: str
    html: str
    text: str


def email_provider() -> str:
    """Proveedor configurado; vacío equivale a `disabled`."""
    provider = (os.getenv("EMAIL_PROVIDER") or "disabled").strip().lower()
    if provider not in PROVIDERS:
        raise RuntimeError(f"EMAIL_PROVIDER must be one of: {', '.join(PROVIDERS)}.")
    return provider


def validate_email_config() -> None:
    """Falla al arrancar si el proveedor elegido no está bien configurado (en lugar de fallar en el primer envío)."""
    if email_provider() == "resend" and not (os.getenv("RESEND_API_KEY") or "").strip():
        raise RuntimeError("RESEND_API_KEY must be set when EMAIL_PROVIDER=resend.")


def frontend_base_url() -> str:
    """Base de los enlaces del correo, sin barra final."""
    return (os.getenv("FRONTEND_BASE_URL") or DEFAULT_FRONTEND_BASE_URL).strip().rstrip("/")


def reset_link(token: str) -> str:
    """Enlace que recibe la persona: `<FRONTEND_BASE_URL>/reset-password?token=<token>`."""
    return f"{frontend_base_url()}/reset-password?token={token}"


def build_reset_email(to: str, name: str, link: str, minutes: int) -> EmailMessage:
    """Correo de recuperación: una sola columna, texto de 16 px y un botón grande, legible en el móvil."""
    greeting = f"Hi {name}," if name else "Hi,"
    safe_greeting, safe_link = html.escape(greeting), html.escape(link, quote=True)
    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reset your TrackFlow password</title>
</head>
<body style="margin:0;padding:0;background:#f4f7f3;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f7f3;">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background:#ffffff;border-radius:8px;border:1px solid #dfe6df;">
<tr><td style="padding:28px 24px;font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.5;color:#1f2a24;">
<p style="margin:0 0 16px;font-size:20px;font-weight:bold;">Reset your password</p>
<p style="margin:0 0 16px;">{safe_greeting}</p>
<p style="margin:0 0 24px;">We received a request to reset the password of your TrackFlow account. Use the button below to choose a new one. The link works once and expires in {minutes} minutes.</p>
<p style="margin:0 0 24px;"><a href="{safe_link}" style="display:inline-block;padding:14px 24px;background:#1f6f4a;color:#ffffff;text-decoration:none;border-radius:6px;font-weight:bold;">Choose a new password</a></p>
<p style="margin:0 0 8px;font-size:14px;color:#55635b;">If the button does not work, copy this address into your browser:</p>
<p style="margin:0 0 24px;font-size:14px;word-break:break-all;"><a href="{safe_link}" style="color:#1f6f4a;">{safe_link}</a></p>
<p style="margin:0;font-size:14px;color:#55635b;">If you did not ask for this, you can ignore this email: your password stays the same.</p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""
    text = (
        f"{greeting}\n\n"
        "We received a request to reset the password of your TrackFlow account.\n"
        f"Open this link to choose a new one (it works once and expires in {minutes} minutes):\n\n"
        f"{link}\n\n"
        "If you did not ask for this, you can ignore this email: your password stays the same.\n"
    )
    return EmailMessage(to=to, subject="Reset your TrackFlow password", html=body, text=text)


def _send_with_resend(message: EmailMessage) -> bool:
    """Una llamada HTTPS a Resend; devuelve True si Resend aceptó el correo."""
    api_key = (os.getenv("RESEND_API_KEY") or "").strip()
    if not api_key:
        logger.error("Password reset email not sent: RESEND_API_KEY is not set.")
        return False
    try:
        response = httpx.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": os.getenv("EMAIL_FROM") or DEFAULT_FROM,
                "to": [message.to],
                "subject": message.subject,
                "html": message.html,
                "text": message.text,
            },
            timeout=SEND_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as error:
        logger.error("Password reset email not sent: could not reach Resend (%s).", type(error).__name__)
        return False
    if response.status_code >= 400:
        # Solo el código: el cuerpo puede repetir direcciones de correo y nunca la clave ni el enlace.
        logger.error("Password reset email not sent: Resend answered HTTP %s.", response.status_code)
        return False
    return True


def send_email(message: EmailMessage) -> bool:
    """Envía el correo con el proveedor configurado. Nunca lanza: devuelve False y deja el motivo en el log.

    Se ejecuta en segundo plano; un fallo de entrega no debe cambiar la respuesta de la API (no revelar qué cuentas existen).
    """
    try:
        provider = email_provider()
    except RuntimeError:
        logger.error("Password reset email not sent: EMAIL_PROVIDER is invalid.")
        return False
    if provider == "resend":
        return _send_with_resend(message)
    if provider == "console":
        # Solo para desarrollo: el enlace es un secreto, así que este modo hay que pedirlo expresamente.
        logger.warning("[email:console] To: %s | Subject: %s\n%s", message.to, message.subject, message.text)
        return True
    logger.warning("Password reset email not sent: EMAIL_PROVIDER is not configured (set it to 'resend').")
    return False
