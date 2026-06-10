import base64
import os
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from googleapiclient.discovery import build


def _sanitize_email(addr: str) -> str:
    """Strip whitespace and control characters from an email address."""
    addr = addr.strip()
    # Remove any control/non-printable characters (including \r \n \t)
    addr = re.sub(r"[\x00-\x1f\x7f]", "", addr)
    return addr.strip()

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

INDONESIAN_MONTHS = [
    "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
]


def _month_tokens(report_period):
    """Return (bulan, bulan_lalu, year) for the email template.

    'Mei 2026' → ('Mei', 'April', '2026')
    """
    parts = report_period.strip().split()
    bulan = parts[0]  # e.g. 'Mei'
    year = parts[1]   # e.g. '2026'

    # Find previous month name
    idx = next(
        (i for i, m in enumerate(INDONESIAN_MONTHS) if m.lower() == bulan.lower()),
        None,
    )
    if idx is None:
        bulan_lalu = ""
    elif idx == 0:
        bulan_lalu = INDONESIAN_MONTHS[11]  # Desember of previous year
    else:
        bulan_lalu = INDONESIAN_MONTHS[idx - 1]

    return bulan, bulan_lalu, year


def _build_html_body(bulan, bulan_lalu, year, broker, policies_sorted):
    """Build the HTML email body with the policy table."""

    # Build table rows
    table_rows = ""
    for i, (policy_no, info) in enumerate(policies_sorted, start=1):
        table_rows += (
            f"<tr>"
            f"<td style='border:1px solid #ccc;padding:4px 8px;'>{i}</td>"
            f"<td style='border:1px solid #ccc;padding:4px 8px;'>{policy_no}</td>"
            f"<td style='border:1px solid #ccc;padding:4px 8px;'>{info.get('company_name','')}</td>"
            f"<td style='border:1px solid #ccc;padding:4px 8px;'>{info.get('policy_effective_date','')}</td>"
            f"<td style='border:1px solid #ccc;padding:4px 8px;'>{info.get('policy_renewal_date','')}</td>"
            f"</tr>"
        )

    jolly_paragraph = "" if "andika" in broker.lower() else """<br>
<p>Sekaligus kami sampaikan bahwa untuk periode laporan selanjutnya, report akan kami
support melalui aplikasi Jolly HR yang dapat diakses secara real time.<br>
Sehubungan dengan hal tersebut, kami mohon bantuan untuk dapat menginformasikan email
PIC atau penanggung jawab yang akan diberikan akses terhadap laporan tersebut.</p>
"""

    html = f"""<html><body>
<p>Dear Bapak/Ibu,</p>

<p>Berikut kami kirimkan Report Claim Detail Insured - {bulan} {year} {broker}
dengan as of {bulan_lalu} {year} dengan detail perusahaan berikut :</p>

<table style='border-collapse:collapse;font-family:Arial,sans-serif;font-size:13px;'>
  <thead>
    <tr style='background:#f2f2f2;'>
      <th style='border:1px solid #ccc;padding:4px 8px;'>No</th>
      <th style='border:1px solid #ccc;padding:4px 8px;'>Policy No</th>
      <th style='border:1px solid #ccc;padding:4px 8px;'>Company Name</th>
      <th style='border:1px solid #ccc;padding:4px 8px;'>Policy Effective Date</th>
      <th style='border:1px solid #ccc;padding:4px 8px;'>Policy Renewal Date</th>
    </tr>
  </thead>
  <tbody>
    {table_rows}
  </tbody>
</table>
{jolly_paragraph}
<p>Demikian informasi yang dapat kami sampaikan.<br>
Atas perhatian dan kerjasamanya yang baik kami ucapkan terima kasih.</p>
</body></html>"""

    return html


def create_drafts(credentials, master, upload_results, report_period):
    """Create one Gmail draft per unique (To, Cc) combination.

    Policies sharing the same To+Cc are bundled into one draft regardless of
    source_name (unlike the previous behaviour).  Each draft:
      - Subject : Report Claim Insured - {bulan} {year} - {broker}
      - Body    : HTML with fixed Indonesian copy + policy table
      - Attached: one .xlsx per policy in the group

    Args:
        credentials:   OAuth2 credentials (must have gmail.compose scope).
        master:        dict[policy_no] → {"to", "cc", "need_report", "row"}
                       from sheets.read_master().
        upload_results: dict[policy_no] → {file_path, web_view_link, company_name,
                        source_name, policy_effective_date, policy_renewal_date, ...}
        report_period: e.g. "Mei 2026"

    Returns:
        Number of drafts created.
    """
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    bulan, bulan_lalu, year = _month_tokens(report_period)

    # Group by (to, cc) — source_name no longer splits groups
    groups: dict[tuple, dict] = {}
    for policy_no, info in upload_results.items():
        if policy_no not in master:
            continue
        recipients = master[policy_no]
        to_key = tuple(sorted(recipients["to"]))
        cc_key = tuple(sorted(recipients["cc"]))
        key = (to_key, cc_key)
        if key not in groups:
            groups[key] = {
                "to": recipients["to"],
                "cc": recipients["cc"],
                "policies": [],
            }
        groups[key]["policies"].append((policy_no, info))

    draft_count = 0
    skipped = 0

    for key, group in groups.items():
        if not group["to"]:
            skipped += len(group["policies"])
            continue

        policies_sorted = sorted(group["policies"], key=lambda x: x[0])

        # Broker = unique source_names in this group, alphabetically joined
        source_names = sorted({info.get("source_name", "") for _, info in policies_sorted if info.get("source_name", "")})
        broker = " / ".join(source_names) if source_names else ""

        subject = f"Report Claim Insured - {bulan} {year} - {broker}" if broker else f"Report Claim Insured - {bulan} {year}"

        html_body = _build_html_body(bulan, bulan_lalu, year, broker, policies_sorted)

        to_clean = [_sanitize_email(e) for e in group["to"] if _sanitize_email(e)]
        cc_clean = [_sanitize_email(e) for e in group["cc"] if _sanitize_email(e)]

        if not to_clean:
            skipped += len(group["policies"])
            print(f"⚠️  Skipping draft '{subject}' — To addresses became empty after sanitization (raw: {group['to']})")
            continue

        msg = MIMEMultipart()
        msg["To"] = ", ".join(to_clean)
        if cc_clean:
            msg["Cc"] = ", ".join(cc_clean)
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        for policy_no, info in policies_sorted:
            file_path = info.get("file_path", "")
            if not file_path or not os.path.exists(file_path):
                continue
            with open(file_path, "rb") as f:
                part = MIMEBase("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                "attachment",
                filename=os.path.basename(file_path),
            )
            msg.attach(part)

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        try:
            service.users().drafts().create(
                userId="me",
                body={"message": {"raw": raw}},
            ).execute()
            draft_count += 1
            print(f"📝 Draft: {subject}  →  To: {', '.join(to_clean)}")
        except Exception as exc:
            print(f"❌ Failed to create draft '{subject}' (To: {to_clean}): {exc}")

    print(f"\n📬 {draft_count} draft(s) created in Gmail.")
    if skipped:
        print(f"⚠️  {skipped} policies skipped — no To recipients in master sheet.")

    return draft_count
