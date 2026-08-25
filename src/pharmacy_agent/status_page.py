"""Judge-facing status page (PRD S7.11 / T56): a minimal, read-only view --
separate from the pharmacist's own WhatsApp/Drive workflow -- listing every
bill the agent has processed with its vendor, timestamp, and terminal
status, linking each to its Cloud Trace reasoning chain (`trace_id`, T54).

It exists because Cloud Trace and Cloud Logging need GCP IAM access a judge
won't have; deployed to allow unauthenticated reads (T57), this page is
meant to be independently checkable proof the backend actually ran on
Google Cloud, rather than trust-the-recording. No auth data or file
contents are ever rendered -- only the same status/findings/vendor/invoice
metadata `agent/terminal.py::record_bill_result` already writes to the
`bills` collection.

Every rendered field ultimately originates from a vendor's uploaded
document (vendor name, invoice number, findings text) -- content this
agent's own guardrails already treat as untrusted (PRD S7.10: "extracted
line items, prices, and free text are validation inputs, not tool-call
directives"). The same rule applies here: nothing pulled from `bills` is
trusted to be safe HTML, so every field is escaped before being placed in
the page.
"""
from __future__ import annotations

import dataclasses
import html

from google.cloud import firestore

from .firestore_client import bills_collection

DEFAULT_LIMIT = 100


@dataclasses.dataclass
class BillSummary:
    doc_id: str
    vendor: str | None
    invoice_number: str | None
    status: str
    findings: list[str]
    trace_id: str | None
    updated_at: str | None


def _summarize(doc: firestore.DocumentSnapshot) -> BillSummary:
    data = doc.to_dict() or {}
    return BillSummary(
        doc_id=doc.id,
        vendor=data.get("vendor"),
        invoice_number=data.get("invoice_number"),
        status=data.get("status", "unknown"),
        findings=list(data.get("findings") or []),
        trace_id=data.get("trace_id"),
        updated_at=doc.update_time.isoformat() if doc.update_time else None,
    )


def list_bills(client: firestore.Client | None = None, limit: int = DEFAULT_LIMIT) -> list[BillSummary]:
    """Most-recently-updated bills first. Sorted in Python on the Firestore
    snapshot's own `update_time` rather than an `order_by` query clause --
    that metadata isn't a stored field a query can sort on, and at the scale
    this page is meant for (a demo's worth of bills, not production volume)
    fetching up to `limit` docs and sorting locally is simpler than adding a
    redundant timestamp field purely to support ordering."""
    docs = bills_collection(client).limit(limit).stream()
    summaries = [_summarize(doc) for doc in docs]
    summaries.sort(key=lambda b: b.updated_at or "", reverse=True)
    return summaries


def cloud_trace_url(project_id: str, trace_id: str) -> str:
    return f"https://console.cloud.google.com/traces/list?tid={trace_id}&project={project_id}"


def _e(value: object) -> str:
    return html.escape(str(value)) if value is not None else ""


def _bill_row_html(bill: BillSummary, project_id: str) -> str:
    trace_cell = (
        f'<a href="{_e(cloud_trace_url(project_id, bill.trace_id))}">{_e(bill.trace_id)}</a>'
        if bill.trace_id
        else "&mdash;"
    )
    findings_html = "".join(f"<li>{_e(f)}</li>" for f in bill.findings)
    return f"""<tr>
<td>{_e(bill.vendor) or '&mdash;'}</td>
<td>{_e(bill.invoice_number) or '&mdash;'}</td>
<td>{_e(bill.updated_at) or '&mdash;'}</td>
<td class="status status-{_e(bill.status)}">{_e(bill.status)}</td>
<td><ul>{findings_html}</ul></td>
<td>{trace_cell}</td>
</tr>"""


def render_status_page(bills: list[BillSummary], project_id: str) -> str:
    rows_html = "\n".join(_bill_row_html(b, project_id) for b in bills)
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Pharmacy Bill Agent -- Status</title>
<style>
body {{ font-family: sans-serif; margin: 2rem; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ccc; padding: 0.5rem; text-align: left; vertical-align: top; }}
th {{ background: #f0f0f0; }}
.status-resolved {{ color: #0a7d2c; }}
.status-pending_pharmacist, .status-pending_vendor {{ color: #b45309; }}
</style>
</head>
<body>
<h1>Pharmacy Bill Agent &mdash; Bill Status</h1>
<p>Read-only view. Status and metadata only -- no file contents or auth data.</p>
<table>
<thead>
<tr><th>Vendor</th><th>Invoice</th><th>Last Updated (UTC)</th><th>Status</th><th>Findings</th><th>Cloud Trace</th></tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>
</body>
</html>"""
