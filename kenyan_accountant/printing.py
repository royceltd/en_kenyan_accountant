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
# (erpnext/setup/install.py set_default_print_formats, v16): "... with Item Image"
# until 16.36, "... Classic" from 16.5x. Not a client's choice, so ours replaces it;
# anything else is the client's and is left alone.
FRAMEWORK_DEFAULT_PRINT_FORMATS = {
	"Sales Invoice": ("Sales Invoice with Item Image", "Sales Invoice Classic"),
	"Purchase Order": ("Purchase Order with Item Image", "Purchase Order Classic"),
	"Purchase Invoice": ("Purchase Invoice with Item Image", "Purchase Invoice Classic"),
	"Quotation": ("Quotation with Item Image", "Quotation Classic"),
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
		if current and current not in FRAMEWORK_DEFAULT_PRINT_FORMATS.get(doctype, ()):
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
	# A local address prints without its country; a foreign one keeps it.
	home_country = (frappe.db.get_value("Company", company, "country") if company else None) or "Kenya"
	bill_to = address_block(party_address(doc), home_country)
	bill_to.phone = bill_to.phone or doc.get("contact_mobile") or doc.get("contact_phone") or ""
	bill_to.email = bill_to.email or doc.get("contact_email") or ""
	return frappe._dict(
		title=document_title(doc),
		badge=document_badge(doc),
		accent=settings.get("print_accent_color") or NEUTRAL_ACCENT,
		payment_details=settings.get("payment_details") or "",
		company=_company_identity(doc, company, home_country),
		party_address=bill_to,
		ship_to=_ship_to(doc, bill_to, home_country),
		amount=amount_box(doc),
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


def amount_box(doc) -> frappe._dict:
	"""The one figure the reader came for, shown framed beside the Bill To block:
	what's still owed on an invoice, the total on a quotation/order, the money on a
	receipt. label and note are untranslated; the template translates them."""
	fmt = frappe.utils.formatdate
	grand = flt(doc.get("rounded_total") or doc.get("grand_total"))
	submitted = cint(doc.get("docstatus")) == 1
	box = frappe._dict(label="Total", amount=grand, currency=doc.get("currency"), note="", note_args=[])

	if doc.doctype == "Payment Entry":
		receiving = doc.get("payment_type") == "Receive"
		box.amount = flt(doc.get("received_amount") if receiving else doc.get("paid_amount"))
		box.currency = doc.get("paid_to_account_currency" if receiving else "paid_from_account_currency")
		box.label = {"Receive": "Amount Received", "Internal Transfer": "Amount Transferred"}.get(doc.get("payment_type"), "Amount Paid")
		box.note = frappe.utils.money_in_words(box.amount, box.currency)
	elif doc.doctype in ("Sales Invoice", "Purchase Invoice") and doc.get("is_return"):
		box.label = "Credit Note Total" if doc.doctype == "Sales Invoice" else "Debit Note Total"
		box.amount = abs(grand)
	elif doc.doctype in ("Sales Invoice", "Purchase Invoice"):
		box.label = "Amount Due"
		box.amount = flt(doc.get("outstanding_amount")) if submitted else grand
		if submitted and box.amount <= 0 and grand > 0:
			box.note = "Paid in full"
		elif doc.get("due_date"):
			box.note, box.note_args = "Due by {0}", [fmt(doc.due_date)]
	elif doc.doctype == "Quotation":
		box.label = "Quotation Total"
		if doc.get("valid_till"):
			box.note, box.note_args = "Valid until {0}", [fmt(doc.valid_till)]
	elif doc.doctype == "Purchase Order":
		box.label = "Order Total"
		if doc.get("schedule_date"):
			box.note, box.note_args = "Required by {0}", [fmt(doc.schedule_date)]
	return box


# "P.O. Box 45210", "P. O. Box 45210-00100", "Box 3456": wherever the user typed it.
PO_BOX = re.compile(r"\b(?:P\.?\s*O\.?\s*)?Box\s*(?:No\.?\s*)?(\d+)(?:\s*[-–]\s*(\d{5}))?", re.IGNORECASE)
POSTAL_CODE = re.compile(r"^\d{5}$")
CONTACT_LABELS = {"phone": "phone", "tel": "phone", "mobile": "phone", "email": "email", "e-mail": "email", "fax": None}


def address_block(address_html, home_country="Kenya") -> frappe._dict:
	"""Frappe's address display (the snapshot saved on the document, so a printed
	invoice keeps the address it was issued to) as the lines a Kenyan reader expects:

	    Viking House, 3rd Floor
	    Waiyaki Way, Westlands
	    P.O. Box 45210-00100, Nairobi

	The address template's "Phone: ..."/"Email: ..." lines come back separately, the
	home country is dropped, and a postal code joins its P.O. Box (or its town)."""
	block = frappe._dict(lines=[], phone="", email="")
	if not address_html:
		return block
	labels = dict(CONTACT_LABELS)
	labels.update({frappe._(label.title()).lower(): kind for label, kind in CONTACT_LABELS.items()})

	lines, country = [], ""
	box = box_postal = box_town = box_at = None
	postal = locality = None
	for raw in re.split(r"<br\s*/?>|\n", address_html):
		line = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", raw)).strip(" ,;")
		if not line:
			continue
		label, sep, value = line.partition(":")
		if sep and label.strip().lower() in labels:
			kind = labels[label.strip().lower()]
			if kind and value.strip() and not block[kind]:
				block[kind] = value.strip()
			continue
		if home_country and line.lower() == home_country.lower():
			continue
		# A postal code follows its town in the template. A first line of digits is
		# whatever the user typed as Address Line 1 (a box or plot number), kept as is.
		if lines and POSTAL_CODE.match(line):
			postal = postal or line
			locality = len(lines) - 1
			continue
		match = PO_BOX.search(line) if box is None else None
		if match:
			box, box_postal = match.group(1), match.group(2)
			before, after = _trim(line[: match.start()]), _trim(line[match.end() :])
			if before:
				lines.append(before)
			box_town, box_at = after, len(lines)
			continue
		if not country and frappe.db.exists("Country", line):
			country = line
			continue
		lines.append(line)

	if postal and locality is not None and not (box is not None and not box_town):
		lines[locality] = f"{lines[locality]} {postal}"
		postal = None
	if box is not None and box_town:
		# Written with its own town ("P.O. Box 262-20400, Bomet"): print it as typed. The
		# post office can be in a different town from the premises (City/Town).
		lines.append(f"P.O. Box {box}" + (f"-{box_postal}" if box_postal else "") + f", {box_town}")
	elif box is not None:
		# A bare box takes its postal code and town from the template's own lines: the
		# line before a separate postal code, else the last line after the box.
		code = box_postal or postal
		if locality is None and lines and len(lines) - 1 >= box_at:
			locality = len(lines) - 1
		text = f"P.O. Box {box}" + (f"-{code}" if code else "")
		if locality is not None:
			text += ", " + lines.pop(locality)
		lines.append(text)
	elif postal:
		lines.append(postal)
	if country:
		lines.append(country)
	block.lines = lines
	return block


def _trim(text) -> str:
	return re.sub(r"^[\s,;.\-–]+|[\s,;.\-–]+$", "", re.sub(r"\s+", " ", text))


def compact_address(address_html) -> str:
	"""The address on one line, for the few places that need it that way."""
	return ", ".join(address_block(address_html).lines)


def _ship_to(doc, bill_to, home_country) -> frappe._dict | None:
	"""The delivery address, only when it isn't the billing address."""
	# On purchase documents shipping_address is the Link; the text is shipping_address_display.
	field = "shipping_address_display" if doc.doctype in ("Purchase Order", "Purchase Invoice") else "shipping_address"
	block = address_block(doc.get(field), home_country)
	if not block.lines or block.lines == bill_to.lines:
		return None
	return block


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


def _company_identity(doc, company, home_country="Kenya") -> frappe._dict:
	if not company:
		return frappe._dict(name="", logo="", address=address_block(""), tax_id="", phone="", email="", website="")
	values = frappe.db.get_value(
		"Company", company, ["company_name", "company_logo", "tax_id", "phone_no", "email", "website"], as_dict=True
	) or frappe._dict()
	address = address_block(_company_address(doc, company), home_country)
	return frappe._dict(
		name=values.company_name or company,
		logo=_inline_image(values.company_logo),
		address=address,
		tax_id=values.tax_id or "",
		# The Company's own contact details first; the address's when it has none.
		phone=values.phone_no or address.phone,
		email=values.email or address.email,
		website=values.website or "",
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
	branch = doc.get("royce_etims_branch")
	return frappe._dict(
		invoice_number=invoice_number,
		# The control unit (SCU) that signed it, and its own receipt counter -
		# what KRA's receipt layout calls the CU ID and CU invoice number.
		cu_id=(frappe.db.get_value("eTIMS Branch", branch, "sdc_id") if branch else "") or "",
		receipt_number=doc.get("royce_etims_current_receipt_number") or "",
		internal_data=doc.get("royce_etims_internal_data") or "",
		signature=doc.get("royce_etims_receipt_signature") or "",
		datetime=_cu_datetime(doc.get("royce_etims_control_unit_datetime")),
		qr=_qr_data_uri(url) if url else "",
	)


def _cu_datetime(value) -> str:
	"""KRA returns sdcDateTime as YYYYMMDDHHMMSS; printed as DD-MM-YYYY HH:MM:SS.
	Anything else is printed as received rather than guessed at."""
	text = str(value or "")
	if len(text) == 14 and text.isdigit():
		return f"{text[6:8]}-{text[4:6]}-{text[0:4]} {text[8:10]}:{text[10:12]}:{text[12:14]}"
	return text


def _qr_data_uri(text: str) -> str:
	try:
		import io

		import pyqrcode

		buffer = io.BytesIO()
		pyqrcode.create(text).png(buffer, scale=3, quiet_zone=1)
		return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
	except Exception:
		return ""
