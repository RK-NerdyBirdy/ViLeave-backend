"""
app/services/email_service.py
──────────────────────────────
Async Gmail SMTP email service using aiosmtplib.

Sends HTML emails for every state transition in the leave workflow:
  1. Proctor notification   — on new submission (PENDING_PROCTOR)
  2. HOD notification       — on proctor approval (PENDING_HOD)
  3. Student rejected       — on proctor OR HOD rejection
  4. Student approved       — on HOD approval
  5. Proctor HOD-rejected   — proctor is also notified when HOD rejects

HTML templates are inline strings here for simplicity. For a larger project,
move them to app/templates/*.html and load via Jinja2.
"""
import asyncio
from dataclasses import dataclass
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Sequence

import aiosmtplib
import logging
import asyncio

logger = logging.getLogger(__name__)
from app.config import get_settings

settings = get_settings()


# ── Data containers ───────────────────────────────────────────────────────────

@dataclass
class EmailPayload:
    to: list[str]
    subject: str
    html_body: str


# ── HTML template helpers ─────────────────────────────────────────────────────

def _base_template(title: str, body_html: str, color: str = "#2563EB") -> str:
    """Minimal responsive HTML email wrapper."""
    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:24px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border-radius:8px;overflow:hidden;
                    box-shadow:0 1px 3px rgba(0,0,0,0.1);">
        <!-- Header -->
        <tr>
          <td style="background:{color};padding:24px 32px;">
            <h1 style="margin:0;color:#ffffff;font-size:20px;font-weight:600;">
              {title}
            </h1>
            <p style="margin:4px 0 0;color:rgba(255,255,255,0.85);font-size:13px;">
              Medical Leave Management System
            </p>
          </td>
        </tr>
        <!-- Body -->
        <tr>
          <td style="padding:28px 32px;color:#374151;font-size:15px;line-height:1.6;">
            {body_html}
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="padding:16px 32px;background:#f9fafb;border-top:1px solid #e5e7eb;">
            <p style="margin:0;color:#9ca3af;font-size:12px;">
              This is an automated message from the Medical Leave System.
              Please do not reply to this email.
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _detail_row(label: str, value: str) -> str:
    return f"""
    <tr>
      <td style="padding:6px 0;color:#6b7280;font-size:13px;width:140px;
                 vertical-align:top;font-weight:600;">{label}</td>
      <td style="padding:6px 0;color:#111827;font-size:13px;">{value}</td>
    </tr>"""


def _details_table(*rows: str) -> str:
    return f"""
    <table cellpadding="0" cellspacing="0"
           style="width:100%;background:#f9fafb;border-radius:6px;
                  border:1px solid #e5e7eb;padding:8px 16px;margin:16px 0;">
      {"".join(rows)}
    </table>"""


def _pdf_button(pdf_url: str) -> str:
    return f"""
    <p>
      <a href="{pdf_url}"
         style="display:inline-block;padding:10px 20px;background:#2563EB;
                color:#ffffff;text-decoration:none;border-radius:6px;
                font-size:14px;font-weight:600;">
        View Medical Certificate (PDF)
      </a>
      <span style="display:block;margin-top:6px;color:#9ca3af;font-size:11px;">
        This link expires in 15 minutes.
      </span>
    </p>"""


# ── Template builders ─────────────────────────────────────────────────────────

def _proctor_new_request_html(
    proctor_honorific: str,
    proctor_name: str,
    student_name: str,
    student_reg_no: str,
    start_date: date,
    end_date: date,
    duration_days: int,
    pdf_url: str,
) -> str:
    body = f"""
    <p>Dear {proctor_honorific} {proctor_name},</p>
    <p>A new medical leave request has been submitted by one of your students
       and requires your review.</p>
    {_details_table(
        _detail_row("Student", student_name),
        _detail_row("Reg. No.", student_reg_no),
        _detail_row("From", start_date.strftime("%d %b %Y")),
        _detail_row("To", end_date.strftime("%d %b %Y")),
        _detail_row("Duration", f"{duration_days} day{'s' if duration_days != 1 else ''}"),
    )}
    <p>Please review the attached medical certificate and take action on the
       leave management portal.</p>
    {_pdf_button(pdf_url)}
    """
    return _base_template("New Leave Request — Action Required", body, "#2563EB")


def _hod_pending_review_html(
    student_name: str,
    student_reg_no: str,
    start_date: date,
    end_date: date,
    duration_days: int,
    proctor_honorific: str,
    proctor_name: str,
    proctor_remarks: str | None,
    pdf_url: str,
) -> str:
    remarks_row = _detail_row(
        "Proctor Remarks",
        proctor_remarks or "<em style='color:#9ca3af;'>No remarks provided</em>"
    )
    body = f"""
    <p>Dear HOD,</p>
    <p>A medical leave request has been approved by the student's proctor and
       now requires your final decision.</p>
    {_details_table(
        _detail_row("Student", student_name),
        _detail_row("Reg. No.", student_reg_no),
        _detail_row("From", start_date.strftime("%d %b %Y")),
        _detail_row("To", end_date.strftime("%d %b %Y")),
        _detail_row("Duration", f"{duration_days} day{'s' if duration_days != 1 else ''}"),
        _detail_row("Proctor", f"{proctor_honorific} {proctor_name}"),
        remarks_row,
    )}
    {_pdf_button(pdf_url)}
    """
    return _base_template("Leave Request — HOD Approval Required", body, "#7C3AED")


def _student_approved_html(student_name: str, start_date: date, end_date: date, duration_days: int) -> str:
    body = f"""
    <p>Dear {student_name},</p>
    <p>Your medical leave request has been <strong style="color:#059669;">approved</strong>
       by the Head of Department.</p>
    {_details_table(
        _detail_row("From", start_date.strftime("%d %b %Y")),
        _detail_row("To", end_date.strftime("%d %b %Y")),
        _detail_row("Duration", f"{duration_days} day{'s' if duration_days != 1 else ''}"),
        _detail_row("Status", '<span style="color:#059669;font-weight:600;">Approved</span>'),
    )}
    <p>Your medical certificate has been retained in our records.
       Please ensure you fulfil all attendance and academic requirements upon your return.</p>
    """
    return _base_template("Leave Request Approved ✓", body, "#059669")


def _student_rejected_by_proctor_html(
    student_name: str,
    start_date: date,
    end_date: date,
    proctor_honorific: str,
    proctor_name: str,
    remarks: str,
) -> str:
    body = f"""
    <p>Dear {student_name},</p>
    <p>Your medical leave request has been <strong style="color:#DC2626;">rejected</strong>
       by your proctor.</p>
    {_details_table(
        _detail_row("From", start_date.strftime("%d %b %Y")),
        _detail_row("To", end_date.strftime("%d %b %Y")),
        _detail_row("Reviewed by", f"{proctor_honorific} {proctor_name}"),
        _detail_row("Reason", remarks),
    )}
    <p>If you believe this is an error, please contact your proctor directly.</p>
    """
    return _base_template("Leave Request Rejected", body, "#DC2626")


def _student_rejected_by_hod_html(
    student_name: str,
    start_date: date,
    end_date: date,
    hod_remarks: str | None,
) -> str:
    body = f"""
    <p>Dear {student_name},</p>
    <p>Your medical leave request has been <strong style="color:#DC2626;">rejected</strong>
       by the Head of Department.</p>
    {_details_table(
        _detail_row("From", start_date.strftime("%d %b %Y")),
        _detail_row("To", end_date.strftime("%d %b %Y")),
        _detail_row("HOD Remarks", hod_remarks or "No remarks provided"),
    )}
    <p>As per policy, your submitted medical documents have been removed from our system.
       Please contact the HOD's office if you have any questions.</p>
    """
    return _base_template("Leave Request Rejected by HOD", body, "#DC2626")


def _proctor_hod_rejected_html(
    proctor_honorific: str,
    proctor_name: str,
    student_name: str,
    student_reg_no: str,
    start_date: date,
    end_date: date,
    hod_remarks: str | None,
) -> str:
    body = f"""
    <p>Dear {proctor_honorific} {proctor_name},</p>
    <p>A leave request you had approved for your student has been
       <strong style="color:#DC2626;">rejected</strong> by the Head of Department.</p>
    {_details_table(
        _detail_row("Student", student_name),
        _detail_row("Reg. No.", student_reg_no),
        _detail_row("Period", f"{start_date.strftime('%d %b %Y')} – {end_date.strftime('%d %b %Y')}"),
        _detail_row("HOD Remarks", hod_remarks or "No remarks provided"),
    )}
    """
    return _base_template("FYI: Student Leave Request Rejected by HOD", body, "#B45309")


# ── SMTP sender ───────────────────────────────────────────────────────────────

async def _send_email(payload: EmailPayload) -> None:
    """
    Low-level async SMTP sender using aiosmtplib.
    Uses STARTTLS (port 587) — the standard secure Gmail SMTP method.
    """
    msg = MIMEMultipart("alternative")
    msg["From"]    = f"{settings.email_from_name} <{settings.smtp_username}>"
    msg["To"]      = ", ".join(payload.to)
    msg["Subject"] = payload.subject
    msg.attach(MIMEText(payload.html_body, "html", "utf-8"))

    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        start_tls=True,
        username=settings.smtp_username,
        password=settings.smtp_password,
    )


async def _send_safe(payload: EmailPayload, max_retries: int = 3) -> None:
    """
    Attempts to send an email multiple times with exponential backoff.
    """
    for attempt in range(1, max_retries + 1):
        try:
            await _send_email(payload)
            # If successful, break out of the loop
            return 
            
        except Exception as exc:
            logger.error(f"[EMAIL ERROR] Attempt {attempt}/{max_retries} failed for {payload.to}: {exc}")
            
            if attempt == max_retries:
                logger.error(f"[EMAIL FATAL] Giving up on email to {payload.to}. Final error: {exc}")
            else:
                # Wait 2 seconds, then 4 seconds before retrying
                await asyncio.sleep(2 ** attempt)


# ── Public API ────────────────────────────────────────────────────────────────

async def notify_proctor_new_request(
    proctor_email: str,
    proctor_honorific: str,
    proctor_name: str,
    student_name: str,
    student_reg_no: str,
    start_date: date,
    end_date: date,
    duration_days: int,
    pdf_url: str,
) -> None:
    await _send_safe(EmailPayload(
        to=[proctor_email],
        subject=f"[Action Required] Medical Leave Request — {student_name} ({student_reg_no})",
        html_body=_proctor_new_request_html(
            proctor_honorific, proctor_name, student_name, student_reg_no,
            start_date, end_date, duration_days, pdf_url,
        ),
    ))


async def notify_hod_pending_review(
    hod_email: str,
    student_name: str,
    student_reg_no: str,
    start_date: date,
    end_date: date,
    duration_days: int,
    proctor_honorific: str,
    proctor_name: str,
    proctor_remarks: str | None,
    pdf_url: str,
) -> None:
    await _send_safe(EmailPayload(
        to=[hod_email],
        subject=f"[HOD Action Required] Medical Leave — {student_name} ({student_reg_no})",
        html_body=_hod_pending_review_html(
            student_name, student_reg_no, start_date, end_date, duration_days,
            proctor_honorific, proctor_name, proctor_remarks, pdf_url,
        ),
    ))


async def notify_student_approved(
    student_email: str,
    student_name: str,
    start_date: date,
    end_date: date,
    duration_days: int,
) -> None:
    await _send_safe(EmailPayload(
        to=[student_email],
        subject="Your Medical Leave Request Has Been Approved",
        html_body=_student_approved_html(student_name, start_date, end_date, duration_days),
    ))


async def notify_student_rejected_by_proctor(
    student_email: str,
    student_name: str,
    start_date: date,
    end_date: date,
    proctor_honorific: str,
    proctor_name: str,
    remarks: str,
) -> None:
    await _send_safe(EmailPayload(
        to=[student_email],
        subject="Your Medical Leave Request Has Been Rejected",
        html_body=_student_rejected_by_proctor_html(
            student_name, start_date, end_date, proctor_honorific, proctor_name, remarks,
        ),
    ))


async def notify_hod_rejection(
    student_email: str,
    student_name: str,
    proctor_email: str,
    proctor_honorific: str,
    proctor_name: str,
    student_reg_no: str,
    start_date: date,
    end_date: date,
    duration_days: int,
    hod_remarks: str | None,
) -> None:
    """Send two emails concurrently: one to student, one to proctor."""
    await asyncio.gather(
        _send_safe(EmailPayload(
            to=[student_email],
            subject="Your Medical Leave Request Has Been Rejected by HOD",
            html_body=_student_rejected_by_hod_html(
                student_name, start_date, end_date, hod_remarks,
            ),
        )),
        _send_safe(EmailPayload(
            to=[proctor_email],
            subject=f"[FYI] Student Leave Rejected by HOD — {student_name}",
            html_body=_proctor_hod_rejected_html(
                proctor_honorific, proctor_name, student_name, student_reg_no,
                start_date, end_date, hod_remarks,
            ),
        )),
    )
