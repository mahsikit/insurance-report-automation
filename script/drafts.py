import base64
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from googleapiclient.discovery import build

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def create_drafts(credentials, recipient_map, upload_results, report_period):
    """Create one Gmail draft per unique (recipient group + source_name) combination.

    Policies sharing the same To+Cc+source_name are bundled into one draft with
    all Excel files attached. Different source_names always get separate drafts
    even when recipients are identical.

    Args:
        credentials: OAuth2 credentials (must have gmail.compose scope).
        recipient_map: dict[policy_no] → {"to": [str], "cc": [str]}
        upload_results: dict[policy_no] → {file_path, web_view_link, company_name, source_name, ...}
        report_period: human-readable period string e.g. "Mei 2026".

    Returns:
        Number of drafts created.
    """
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)

    # Group by (to, cc, source_name)
    groups: dict[tuple, dict] = {}
    for policy_no, info in upload_results.items():
        if policy_no not in recipient_map:
            continue
        recipients = recipient_map[policy_no]
        to_key = tuple(sorted(recipients["to"]))
        cc_key = tuple(sorted(recipients["cc"]))
        source_name = info.get("source_name", "")
        key = (to_key, cc_key, source_name)
        if key not in groups:
            groups[key] = {
                "to": recipients["to"],
                "cc": recipients["cc"],
                "source_name": source_name,
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
        companies = sorted({info["company_name"] for _, info in policies_sorted})
        source_name = group["source_name"]

        if len(companies) == 1:
            subject = f"Laporan Klaim {report_period} - {companies[0]}"
        else:
            subject = f"Laporan Klaim {report_period}"
        if source_name:
            subject += f" ({source_name})"

        lines = [f"Yth. Tim terkait,\n\nBerikut laporan klaim untuk periode {report_period}:\n"]
        for policy_no, info in policies_sorted:
            lines.append(f"  - {info['company_name']} ({policy_no})\n    {info['web_view_link']}")
        lines.append("\nSilakan menghubungi kami jika ada pertanyaan.\n\nSalam,")
        body = "\n".join(lines)

        msg = MIMEMultipart()
        msg["To"] = ", ".join(group["to"])
        if group["cc"]:
            msg["Cc"] = ", ".join(group["cc"])
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

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
        service.users().drafts().create(
            userId="me",
            body={"message": {"raw": raw}},
        ).execute()

        draft_count += 1
        print(f"📝 Draft: {subject}  →  To: {', '.join(group['to'])}")

    print(f"\n📬 {draft_count} draft(s) created in Gmail.")
    if skipped:
        print(f"⚠️  {skipped} policies skipped — no To recipients in master sheet.")

    return draft_count
