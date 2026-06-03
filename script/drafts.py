import base64
from email.mime.text import MIMEText
from googleapiclient.discovery import build


def create_drafts(credentials, recipient_map, upload_results, report_period):
    """Create one Gmail draft per unique recipient group.

    Groups policies that share the same To+Cc set into a single draft so
    each recipient mailbox receives exactly one message per run.

    Args:
        credentials: OAuth2 credentials (must have gmail.compose scope).
        recipient_map: dict[policy_no] → {"to": [str], "cc": [str]}
        upload_results: dict[policy_no] → {web_view_link, company_name, ...}
        report_period: human-readable period string e.g. "Mei 2026".

    Returns:
        Number of drafts created.
    """
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)

    # Group policies by unique (to, cc) recipient set
    groups: dict[tuple, dict] = {}
    for policy_no, info in upload_results.items():
        if policy_no not in recipient_map:
            continue
        recipients = recipient_map[policy_no]
        key = (tuple(sorted(recipients["to"])), tuple(sorted(recipients["cc"])))
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
        companies = sorted({info["company_name"] for _, info in policies_sorted})

        subject = (
            f"Laporan Klaim {report_period} - {companies[0]}"
            if len(companies) == 1
            else f"Laporan Klaim {report_period}"
        )

        lines = [f"Yth. Tim terkait,\n\nBerikut laporan klaim untuk periode {report_period}:\n"]
        for policy_no, info in policies_sorted:
            lines.append(f"  - {info['company_name']} ({policy_no})\n    {info['web_view_link']}")
        lines.append("\nSilakan menghubungi kami jika ada pertanyaan.\n\nSalam,")
        body = "\n".join(lines)

        msg = MIMEText(body, "plain", "utf-8")
        msg["To"] = ", ".join(group["to"])
        if group["cc"]:
            msg["Cc"] = ", ".join(group["cc"])
        msg["Subject"] = subject

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
