import base64
import os
import re
import sys
from collections import defaultdict
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# =============================================================================
# EMAIL TEMPLATE — edit here to customise subject and body
# Available placeholders:
#   {marketing_name}  — derived from the To email  (e.g. "Jonathan Santoso")
#   {company_name}    — policy holder company name  (e.g. "GUDANG GARAM TBK")
#   {source_name}     — broker / agent name         (e.g. "SOCIETO GENERAL SINERGI")
#   {period}          — report period               (e.g. "Mei 2026")
#   {company_list}    — bullet list of all companies when email covers >1 policy
# =============================================================================

SUBJECT_TEMPLATE = "Laporan Klaim {period} - {company_name}"
SUBJECT_TEMPLATE_MULTI = "Laporan Klaim {period} - Multiple Policies"

BODY_TEMPLATE = """\
Yth. Bapak/Ibu {marketing_name},

Salam sejahtera,

Bersama email ini kami sampaikan Laporan Klaim periode {period} untuk:

  Perusahaan  : {company_name}
  Broker/Agen : {source_name}

Terlampir file laporan klaim dalam format Excel. Mohon untuk dapat diperiksa dan dikonfirmasi.

Apabila terdapat pertanyaan atau hal yang perlu didiskusikan lebih lanjut, jangan ragu untuk menghubungi kami.

Terima kasih atas perhatian dan kerjasamanya.

Salam hormat,
Tim Sunday Insurance\
"""

BODY_TEMPLATE_MULTI = """\
Yth. Bapak/Ibu {marketing_name},

Salam sejahtera,

Bersama email ini kami sampaikan Laporan Klaim periode {period} untuk beberapa polis berikut:

{company_list}

Terlampir file laporan klaim masing-masing dalam format Excel. Mohon untuk dapat diperiksa dan dikonfirmasi.

Apabila terdapat pertanyaan atau hal yang perlu didiskusikan lebih lanjut, jangan ragu untuk menghubungi kami.

Terima kasih atas perhatian dan kerjasamanya.

Salam hormat,
Tim Sunday Insurance\
"""

# =============================================================================


def _derive_name(email: str) -> str:
    """'jonathan.santoso@sundayinsurance.co.id' → 'Jonathan Santoso'"""
    local = email.split("@")[0]
    parts = re.split(r"[.\-_]", local)
    return " ".join(p.capitalize() for p in parts if p)


def _build_message(to_list, cc_list, subject, body, attachments):
    msg = MIMEMultipart()
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    for file_path in attachments:
        with open(file_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(file_path))
        part["Content-Disposition"] = f'attachment; filename="{os.path.basename(file_path)}"'
        msg.attach(part)

    return {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}


def _build_groups(upload_results, recipient_map):
    """Return list of group dicts, each with to/cc/policies keys."""
    groups: dict[tuple, dict] = defaultdict(lambda: {"to": [], "cc": [], "policies": []})

    for policy_no, info in upload_results.items():
        if policy_no not in recipient_map:
            continue
        recipients = recipient_map[policy_no]
        to_list = recipients.get("to", [])
        cc_list = recipients.get("cc", [])
        if not to_list:
            continue
        key = (
            tuple(sorted(t.lower() for t in to_list)),
            tuple(sorted(c.lower() for c in cc_list)),
        )
        groups[key]["to"] = to_list
        groups[key]["cc"] = cc_list
        groups[key]["policies"].append(info)

    return list(groups.values())


def _print_plan(groups, period):
    """Print a preview of what would be sent without calling Gmail."""
    total_recipients = sum(len(g["to"]) + len(g["cc"]) for g in groups)
    print(f"\n📋 EMAIL PLAN — {len(groups)} email(s) to {total_recipients} address(es)\n")
    for i, group in enumerate(groups, 1):
        to_list = group["to"]
        cc_list = group["cc"]
        policies = group["policies"]
        if len(policies) == 1:
            subject = SUBJECT_TEMPLATE.format(
                period=period, company_name=policies[0].get("company_name", "")
            )
        else:
            subject = SUBJECT_TEMPLATE_MULTI.format(period=period)
        print(f"  [{i}] To      : {', '.join(to_list)}")
        if cc_list:
            print(f"      Cc      : {', '.join(cc_list)}")
        print(f"      Subject : {subject}")
        print(f"      Files   : {len(policies)} attachment(s)")
        print()


def send_reports(credentials, upload_results, recipient_map, period, dry_run=False, assume_yes=False):
    """Group policies by (to, cc) pair and send one email per group.

    dry_run=True  → print plan only, no Gmail calls.
    assume_yes=True → skip the interactive confirmation prompt (required in non-TTY environments).
    """
    if not recipient_map:
        print("⚠️  No recipients found — skipping email send.")
        return

    groups = _build_groups(upload_results, recipient_map)
    if not groups:
        print("⚠️  No sendable groups (To column empty?) — skipping email send.")
        return

    _print_plan(groups, period)

    if dry_run:
        print("🔍 Dry run — no emails sent.")
        return

    # Confirm before sending
    if not assume_yes:
        if not sys.stdin.isatty():
            print("❌ Non-interactive environment detected. Pass --yes to send emails non-interactively.")
            return
        answer = input(f"Send {len(groups)} email(s)? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return

    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)

    sent = 0
    errors = 0

    for group in groups:
        to_list = group["to"]
        cc_list = group["cc"]
        policies = group["policies"]

        marketing_name = _derive_name(to_list[0])
        files = [p["file_path"] for p in policies]

        if len(policies) == 1:
            info = policies[0]
            subject = SUBJECT_TEMPLATE.format(
                period=period,
                company_name=info.get("company_name", ""),
            )
            body = BODY_TEMPLATE.format(
                marketing_name=marketing_name,
                period=period,
                company_name=info.get("company_name", ""),
                source_name=info.get("source_name", ""),
            )
        else:
            company_list = "\n".join(
                f"  - {p.get('company_name', '')} (via {p.get('source_name', '')})"
                for p in policies
            )
            subject = SUBJECT_TEMPLATE_MULTI.format(period=period)
            body = BODY_TEMPLATE_MULTI.format(
                marketing_name=marketing_name,
                period=period,
                company_list=company_list,
            )

        try:
            raw_msg = _build_message(to_list, cc_list, subject, body, files)
            service.users().messages().send(userId="me", body=raw_msg).execute()
            print(f"✉️  Sent to {', '.join(to_list)} ({len(files)} file(s))")
            sent += 1
        except HttpError as e:
            errors += 1
            print(f"❌ Failed to send to {', '.join(to_list)}: {e}")

    print(f"\n✉️  EMAIL SELESAI!")
    print(f"✅ Sent   : {sent}")
    if errors:
        print(f"❌ Errors : {errors}")
