# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Everything our printed documents need that a Jinja template shouldn't work out
for itself: the issuing company's identity (logo, address, KRA PIN), the document's
legal title (Tax Invoice / Credit Note / ...), per-line VAT, and the client's own
print settings.

Ownership rule (ADR-023 in royce_ip): our print formats are standard, shipped as
app files, so every fix reaches every client still using them. Which format is the
DEFAULT is the client's choice: set once at install (set_default_print_formats)
and never again, so a client's own default survives every release.
"""

import base64
import json
import re

import frappe
from frappe.utils import cint, flt

DEFAULT_PRINT_FORMATS = {
	"Sales Invoice": "Kenya Tax Invoice",
	"Quotation": "Kenya Quotation",
	"Purchase Order": "Kenya Purchase Order",
	"Purchase Invoice": "Kenya Purchase Invoice",
	"Payment Entry": "Kenya Payment Receipt",
}

# What ERPNext's own installer sets as the default before our app is installed
# (erpnext/setup/install.py set_default_print_formats, v16). Not a client's choice,
# so ours replaces it; anything else is the client's and is left alone.
FRAMEWORK_DEFAULT_PRINT_FORMATS = {
	"Sales Invoice": "Sales Invoice with Item Image",
	"Purchase Order": "Purchase Order with Item Image",
	"Purchase Invoice": "Purchase Invoice with Item Image",
	"Quotation": "Quotation with Item Image",
}

# ERPNext's installer also creates these letterheads and makes the grey one the
# default (erpnext/setup/install.py create_letter_head). Left exactly as shipped they
# are not the client's letterhead, so our own header is used instead of them.
STOCK_LETTER_HEADS = {
	"Company Letterhead": "company_letterhead.html",
	"Company Letterhead - Grey": "company_letterhead_grey.html",
}

# Neutral on purpose: a partner's clients (ADR-022) must not print in Royce's colour.
# A client picks their own in Kenya Accounting Settings > Printed Documents.
NEUTRAL_ACCENT = "#1f2937"

# A logo bigger than this is left out rather than inlined into every PDF.
MAX_LOGO_BYTES = 2 * 1024 * 1024


def set_default_print_formats(formats: dict | None = None) -> list:
	"""Makes each of our print formats its doctype's default, unless the client has
	already chosen one: replaces only an empty default or ERPNext's own install-time
	default. Runs at install, never on migrate, so once a client picks their own
	default ("Set as default" and Customize Form both write the same Property Setter)
	nothing we ship touches it again. Returns the doctypes set."""
	done = []
	for doctype, print_format in (formats or DEFAULT_PRINT_FORMATS).items():
		if not frappe.db.exists("Print Format", print_format):
			continue
		current = frappe.db.get_value(
			"Property Setter",
			{"doc_type": doctype, "doctype_or_field": "DocType", "property": "default_print_format"},
			"value",
		)
		if current and current != FRAMEWORK_DEFAULT_PRINT_FORMATS.get(doctype):
			continue
		frappe.make_property_setter(
			{
				"doctype": doctype,
				"doctype_or_field": "DocType",
				"property": "default_print_format",
				"value": print_format,
				"property_type": "Data",
			},
			is_system_generated=False,
		)
		done.append(doctype)
	return done


def kenya_print_context(doc) -> frappe._dict:
	"""One call at the top of each template (registered as a Jinja method in hooks)."""
	company = doc.get("company")
	settings = _print_settings(company)
	return frappe._dict(
		title=document_title(doc),
		badge=document_badge(doc),
		accent=settings.get("print_accent_color") or NEUTRAL_ACCENT,
		payment_details=settings.get("payment_details") or "",
		company=_company_identity(doc, company),
		party_address=compact_address(party_address(doc)),
		summary=invoice_summary(doc),
		use_letter_head=uses_own_letter_head(doc),
		line_vat=line_vat(doc),
		# VAT-inclusive prices: lines print their VAT-exclusive (net) figures, so
		# "Subtotal (excl. VAT)" + VAT = Total holds either way.
		tax_inclusive=any(cint(tax.get("included_in_print_rate")) for tax in doc.get("taxes") or []),
		etims=_etims(doc),
	)


def document_title(doc) -> str:
	"""The legal name of what's being printed. Kenya's VAT rules require "Tax Invoice"
	on an invoice that charges VAT; a return is a Credit Note, not an invoice."""
	if doc.doctype == "Sales Invoice":
		if doc.get("is_return"):
			return "Credit Note"
		if doc.get("is_debit_note"):
			return "Debit Note"
		return "Tax Invoice" if flt(doc.get("total_taxes_and_charges")) else "Invoice"
	if doc.doctype == "Purchase Invoice":
		return "Debit Note" if doc.get("is_return") else "Purchase Invoice"
	if doc.doctype == "Payment Entry":
		return {
			"Receive": "Payment Receipt",
			"Pay": "Payment Voucher",
			"Internal Transfer": "Transfer Voucher",
		}.get(doc.get("payment_type"), "Payment")
	return doc.doctype


def document_badge(doc) -> str:
	if cint(doc.get("docstatus")) == 0:
		return "Draft"
	if cint(doc.get("docstatus")) == 2:
		return "Cancelled"
	if doc.doctype == "Sales Invoice" and not doc.get("is_return"):
		if flt(doc.get("grand_total")) > 0 and flt(doc.get("outstanding_amount")) <= 0:
			return "Paid"
	return ""


def line_vat(doc) -> dict:
	"""{item row name: {"rate": %, "amount": KES}} for every item row. ERPNext v16
	records exactly how each tax row split across items (item_wise_tax_details);
	older documents fall back to the item's own tax template rates, then to the
	document-wide "On Net Total" rate."""
	rows = {}
	for detail in doc.get("item_wise_tax_details") or []:
		row = rows.setdefault(detail.item_row, {"rate": 0.0, "amount": 0.0})
		row["rate"] += flt(detail.rate)
		row["amount"] += flt(detail.amount)
	if rows:
		return rows

	net_total_rate = sum(
		flt(tax.rate) for tax in doc.get("taxes") or [] if tax.get("charge_type") == "On Net Total"
	)
	for item in doc.get("items") or []:
		rate = net_total_rate
		if item.get("item_tax_rate"):
			try:
				rate = sum(flt(r) for r in json.loads(item.item_tax_rate).values())
			except (ValueError, TypeError, AttributeError):
				pass
		rows[item.name] = {"rate": rate, "amount": flt(item.get("net_amount")) * rate / 100}
	return rows


PARTY_FIELDS = {
	"Sales Invoice": ("Customer", "customer"),
	"Purchase Order": ("Supplier", "supplier"),
	"Purchase Invoice": ("Supplier", "supplier"),
}


def party_address(doc) -> str:
	"""The address chosen on the document; for one saved before the party had an
	address, the party's current primary address."""
	if doc.get("address_display"):
		return doc.address_display
	party_type, field = PARTY_FIELDS.get(doc.doctype, (None, None))
	if doc.doctype == "Quotation" and doc.get("quotation_to") == "Customer":
		party_type, field = "Customer", "party_name"
	if not party_type or not doc.get(field):
		return ""
	try:
		from frappe.contacts.doctype.address.address import get_address_display, get_default_address

		name = get_default_address(party_type, doc.get(field))
		return get_address_display(name) if name else ""
	except Exception:
		return ""


def uses_own_letter_head(doc) -> bool:
	"""Whether the letterhead Frappe will print with is one the client made or edited.
	ERPNext's stock letterheads, untouched, don't count: our header is better."""
	name = doc.get("letter_head") or frappe.db.get_value("Letter Head", {"is_default": 1, "disabled": 0}, "name")
	if not name:
		return False
	stock_file = STOCK_LETTER_HEADS.get(name)
	if not stock_file:
		return True
	try:
		shipped = frappe.read_file(frappe.get_app_path("erpnext", "accounts", "letterhead", stock_file))
	except Exception:
		return True
	return (frappe.db.get_value("Letter Head", name, "content") or "").strip() != (shipped or "").strip()


def invoice_summary(doc) -> frappe._dict | None:
	"""The three things a payer looks for first, for the band under the header of a
	sales invoice. None for anything else (returns included)."""
	if doc.doctype != "Sales Invoice" or doc.get("is_return"):
		return None
	grand = flt(doc.get("rounded_total") or doc.get("grand_total"))
	due = flt(doc.get("outstanding_amount")) if cint(doc.get("docstatus")) == 1 else grand
	return frappe._dict(
		number=doc.name,
		due_date=frappe.utils.formatdate(doc.due_date) if doc.get("due_date") else "",
		amount_due=due,
		currency=doc.get("currency"),
	)


def compact_address(address_html) -> str:
	"""Frappe's address display is one line per field ("Vision Plaza<br>P.O. Box
	...<br>Nairobi<br>Kenya<br>"). On a document header that's five lines of height
	for one fact; print it as one line that wraps naturally."""
	if not address_html:
		return ""
	lines = re.split(r"<br\s*/?>|\n", address_html)
	parts = [re.sub(r"<[^>]+>", "", line).strip().strip(",") for line in lines]
	return ", ".join(part for part in parts if part)


def kenya_tax_label(tax) -> str:
	"""A tax row's description is the accounting template's name ("Kenya Standard
	VAT 16% - Sales"). Customers see "VAT (16%)"."""
	description = tax.get("description") or ""
	if "vat" in description.lower() or "vat" in (tax.get("account_head") or "").lower():
		rate = flt(tax.get("rate"))
		return f"{frappe._('VAT')} ({kenya_pct(rate)})" if rate else frappe._("VAT")
	return description


def kenya_qty(value) -> str:
	"""1.00 -> "1", 2.5 -> "2.5": whole units print without decimals."""
	value = flt(value, 3)
	return f"{value:,.0f}" if value == int(value) else f"{value:,.3f}".rstrip("0")


def kenya_pct(value) -> str:
	"""16.0 -> "16%", 0.5 -> "0.5%"."""
	value = flt(value, 2)
	return (f"{value:.0f}" if value == int(value) else f"{value:.2f}".rstrip("0")) + "%"


def _print_settings(company) -> dict:
	"""Payment details and accent colour from this company's Kenya Accounting Settings.
	Tolerates a site whose settings predate these fields, or has none at all."""
	if not company or not frappe.db.exists("Kenya Accounting Settings", company):
		return {}
	meta = frappe.get_meta("Kenya Accounting Settings")
	fields = [f for f in ("payment_details", "print_accent_color") if meta.has_field(f)]
	if not fields:
		return {}
	return frappe.db.get_value("Kenya Accounting Settings", company, fields, as_dict=True) or {}


def _company_identity(doc, company) -> frappe._dict:
	if not company:
		return frappe._dict(name="", logo="", address="", tax_id="", contacts=[])
	values = frappe.db.get_value(
		"Company", company, ["company_name", "company_logo", "tax_id", "phone_no", "email", "website"], as_dict=True
	) or frappe._dict()
	return frappe._dict(
		name=values.company_name or company,
		logo=_inline_image(values.company_logo),
		address=compact_address(_company_address(doc, company)),
		tax_id=values.tax_id or "",
		# Rendered as separate no-wrap pieces, so a wrap never leaves a dangling "·".
		contacts=[bit for bit in (values.phone_no, values.email, values.website) if bit],
	)


def _company_address(doc, company) -> str:
	"""The address chosen on the document, else the company's own default address."""
	# On purchase documents the company's own address is the billing address;
	# address_display there is the supplier's.
	purchase = doc.doctype in ("Purchase Order", "Purchase Invoice")
	chosen = doc.get("billing_address_display" if purchase else "company_address_display")
	if chosen:
		return chosen
	try:
		from frappe.contacts.doctype.address.address import get_company_address

		return get_company_address(company).get("company_address_display") or ""
	except Exception:
		return ""


def _inline_image(file_url) -> str:
	"""The logo as a data: URI, so the PDF never has to fetch it over the network
	(private files would fail, and it's faster)."""
	if not file_url:
		return ""
	try:
		file_doc = frappe.get_doc("File", {"file_url": file_url})
		content = file_doc.get_content()
	except Exception:
		return ""
	if isinstance(content, str):
		content = content.encode()
	if not content or len(content) > MAX_LOGO_BYTES:
		return ""
	mime = {
		"png": "image/png",
		"jpg": "image/jpeg",
		"jpeg": "image/jpeg",
		"gif": "image/gif",
		"svg": "image/svg+xml",
		"webp": "image/webp",
	}.get(file_url.rsplit(".", 1)[-1].lower())
	if not mime:
		return ""
	return f"data:{mime};base64,{base64.b64encode(content).decode()}"


def _etims(doc) -> frappe._dict:
	"""KRA eTIMS details, printed only once royce_etims has actually signed the invoice."""
	invoice_number = doc.get("royce_etims_invoice_number")
	if not invoice_number:
		return frappe._dict()
	url = doc.get("royce_etims_qr_verification_url") or ""
	return frappe._dict(
		invoice_number=invoice_number,
		signature=doc.get("royce_etims_receipt_signature") or "",
		datetime=doc.get("royce_etims_control_unit_datetime") or "",
		qr=_qr_data_uri(url) if url else "",
	)


def _qr_data_uri(text: str) -> str:
	try:
		import io

		import pyqrcode

		buffer = io.BytesIO()
		pyqrcode.create(text).png(buffer, scale=3, quiet_zone=1)
		return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
	except Exception:
		return ""
