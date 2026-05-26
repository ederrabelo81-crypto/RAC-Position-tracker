"""SMTP config + HTML e-mail building blocks + 'send email' UI section."""

import os
import re
from datetime import date

import streamlit as st

from lib.formatting import _esc


def _smtp_config() -> dict:
    """Read SMTP settings from environment variables (Replit Secrets / .env)."""
    return {
        "host":     os.getenv("SMTP_HOST", "").strip(),
        "port":     os.getenv("SMTP_PORT", "587").strip() or "587",
        "user":     os.getenv("SMTP_USER", "").strip(),
        "password": os.getenv("SMTP_PASS", "").strip(),
        "sender":   os.getenv("SMTP_FROM", "").strip(),
    }


def _smtp_ready(cfg: dict | None = None) -> bool:
    """True when host, user, password and sender are all configured."""
    cfg = cfg or _smtp_config()
    return all(cfg.get(k) for k in ("host", "user", "password", "sender"))


def _parse_recipients(raw: str) -> list[str]:
    """Split a comma/semicolon/newline-separated string into clean addresses."""
    if not raw:
        return []
    return [p.strip() for p in re.split(r"[,;\n]+", raw) if p.strip()]


def _send_email_smtp(
    subject: str,
    html_body: str,
    text_body: str,
    recipients: list[str],
) -> tuple[bool, str]:
    """Send a multipart (text + HTML) e-mail via SMTP. Returns (ok, message)."""
    cfg = _smtp_config()
    if not _smtp_ready(cfg):
        return False, "SMTP não configurado — defina as Replit Secrets."
    if not recipients:
        return False, "Nenhum destinatário válido informado."

    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["sender"]
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        port = int(cfg["port"])
    except ValueError:
        port = 587

    try:
        if port == 465:
            with smtplib.SMTP_SSL(cfg["host"], port, timeout=30) as server:
                server.login(cfg["user"], cfg["password"])
                server.sendmail(cfg["sender"], recipients, msg.as_string())
        else:
            with smtplib.SMTP(cfg["host"], port, timeout=30) as server:
                server.starttls()
                server.login(cfg["user"], cfg["password"])
                server.sendmail(cfg["sender"], recipients, msg.as_string())
        return True, f"E-mail enviado para {len(recipients)} destinatário(s)."
    except Exception as exc:
        return False, f"Falha no envio SMTP: {exc}"


def _render_smtp_help(default_to_key: str) -> None:
    """Render the SMTP setup expander (or a 'configured' note)."""
    cfg = _smtp_config()
    if _smtp_ready(cfg):
        with st.expander("⚙️ SMTP configured", expanded=False):
            st.success(
                f"SMTP pronto — enviando como `{cfg['sender']}` via "
                f"`{cfg['host']}:{cfg['port']}`."
            )
        return
    with st.expander("⚙️ SMTP not configured — click to see what to add",
                     expanded=False):
        st.markdown(
            "Add the following Replit Secrets to enable **Send via SMTP**:"
        )
        st.markdown(
            "| Key | Example |\n"
            "|---|---|\n"
            "| `SMTP_HOST` | `smtp.gmail.com` |\n"
            "| `SMTP_PORT` | `587` (default) |\n"
            "| `SMTP_USER` | `you@gmail.com` |\n"
            "| `SMTP_PASS` | App-password (Gmail → Account → Security "
            "→ App passwords) |\n"
            "| `SMTP_FROM` | `RAC Monitor <you@gmail.com>` |\n"
            f"| `{default_to_key}` | *(optional)* default recipients, "
            "comma-separated |\n"
        )
        st.caption(
            "You can still **preview** and **download** the HTML/text "
            "below without SMTP."
        )


def _badge(label: str, *, bg: str = "#eff6ff", fg: str = "#1a56db",
           border: str = "#bfdbfe") -> None:
    """Render a small rounded pill badge in the main area."""
    st.markdown(
        f"<span style='display:inline-block;background:{bg};color:{fg};"
        f"border:1px solid {border};border-radius:8px;padding:3px 12px;"
        f"font-size:0.85rem;font-weight:600;'>{_esc(label)}</span>",
        unsafe_allow_html=True,
    )


def _email_shell(eyebrow: str, title: str, subtitle: str,
                 accent1: str, accent2: str, body_html: str) -> str:
    """Wrap e-mail body content in a styled, mail-client-safe HTML shell."""
    generated = date.today().strftime("%d/%m/%Y")
    return (
        '<!DOCTYPE html><html><body style="margin:0;padding:0;'
        'background:#f1f5f9;font-family:Arial,Helvetica,sans-serif;">'
        '<div style="max-width:680px;margin:0 auto;padding:24px;">'
        f'<div style="background:linear-gradient(135deg,{accent1},{accent2});'
        'border-radius:12px;padding:24px 28px;color:#ffffff;">'
        f'<div style="font-size:12px;letter-spacing:0.12em;font-weight:700;'
        f'opacity:0.85;">{_esc(eyebrow)}</div>'
        f'<div style="font-size:24px;font-weight:800;margin-top:6px;">'
        f'{_esc(title)}</div>'
        f'<div style="font-size:13px;opacity:0.9;margin-top:4px;">'
        f'{_esc(subtitle)}</div></div>'
        '<div style="background:#ffffff;border-radius:12px;padding:20px 24px;'
        f'margin-top:16px;">{body_html}</div>'
        '<div style="text-align:center;color:#94a3b8;font-size:11px;'
        f'margin-top:16px;">Gerado pelo RAC Price Monitor · {generated}</div>'
        '</div></body></html>'
    )


def _email_table(headers: list[str], rows: list[list[str]],
                 align: list[str]) -> str:
    """Build an HTML <table> for e-mail. Cell strings are inserted verbatim."""
    th = "".join(
        f'<th style="text-align:{align[i]};padding:8px 6px;'
        f'border-bottom:2px solid #e2e8f0;font-size:11px;color:#475569;'
        f'text-transform:uppercase;letter-spacing:0.04em;">{h}</th>'
        for i, h in enumerate(headers)
    )
    body = ""
    for row in rows:
        tds = "".join(
            f'<td style="text-align:{align[i]};padding:8px 6px;'
            f'border-bottom:1px solid #f1f5f9;font-size:12px;'
            f'color:#1e293b;">{cell}</td>'
            for i, cell in enumerate(row)
        )
        body += f"<tr>{tds}</tr>"
    return (
        '<table style="width:100%;border-collapse:collapse;margin-top:6px;">'
        f'<thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'
    )


def _render_send_section(
    html_body: str,
    text_body: str,
    *,
    subject: str,
    filename_stub: str,
    default_to_env: str,
    send_button_label: str,
    state_prefix: str,
    recipients_raw: str | None = None,
    recipients_in_section: bool = False,
    show_smtp_help: bool = True,
) -> None:
    """Render the shared 'Send as email' block: preview, downloads, send."""
    st.divider()
    st.subheader("📨 Send as email")

    with st.expander("Preview email", expanded=False):
        tab_html, tab_text = st.tabs(["HTML", "Plain text"])
        with tab_html:
            import streamlit.components.v1 as components
            components.html(html_body, height=520, scrolling=True)
        with tab_text:
            st.code(text_body, language=None)

    dl1, dl2 = st.columns(2)
    dl1.download_button(
        "⬇️ Download HTML", data=html_body.encode("utf-8"),
        file_name=f"{filename_stub}.html", mime="text/html",
        use_container_width=True, key=f"{state_prefix}_dl_html",
    )
    dl2.download_button(
        "⬇️ Download text", data=text_body.encode("utf-8"),
        file_name=f"{filename_stub}.txt", mime="text/plain",
        use_container_width=True, key=f"{state_prefix}_dl_txt",
    )

    if recipients_in_section:
        recipients_raw = st.text_input(
            "Recipients (comma-separated)",
            value=os.getenv(default_to_env, ""),
            placeholder="alice@example.com, bob@example.com",
            key=f"{state_prefix}_recipients",
        )

    if show_smtp_help:
        _render_smtp_help(default_to_env)

    recipients = _parse_recipients(recipients_raw or "")
    if not recipients:
        recipients = _parse_recipients(os.getenv(default_to_env, ""))

    smtp_ok = _smtp_ready()
    if st.button(f"📧 {send_button_label}", type="primary",
                 disabled=not smtp_ok, use_container_width=False,
                 key=f"{state_prefix}_send"):
        if not recipients:
            st.warning("Informe ao menos um destinatário antes de enviar.")
        else:
            ok, msg = _send_email_smtp(subject, html_body, text_body,
                                       recipients)
            (st.success if ok else st.error)(msg)
    if not smtp_ok:
        st.caption(
            "Configure SMTP (see expander) to enable sending — "
            "preview and downloads work without it."
        )
