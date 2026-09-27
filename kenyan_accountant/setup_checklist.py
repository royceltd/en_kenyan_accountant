# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""The "finish setting up your documents" checklist a new client sees on the desk.

Provisioning creates the Company from the signup's business name only; the logo,
KRA PIN, address, contacts and payment details that make an invoice usable have to
come from the client. Shown to System Managers and Accounts Managers until each
item is done (it's recomputed on every desk load) or they choose "Don't show again".
"""

import frappe

CHECKLIST_ROLES = {"System Manager", "Accounts Manager"}
DISMISSED_KEY = "kenya_setup_checklist_dismissed"


def extend_bootinfo(bootinfo):
	"""Never allowed to break the desk: any failure just means no checklist."""
	try:
		if frappe.session.user == "Guest" or not CHECKLIST_ROLES & set(frappe.get_roles()):
			return
		if frappe.defaults.get_user_default(DISMISSED_KEY):
			return
		items = pending_items()
		if items:
			bootinfo.kenya_setup_checklist = items
	except Exception:
		frappe.logger("kenyan_accountant").exception("setup checklist: skipped")


def pending_items(company: str | None = None) -> list:
	company = (
		company
		or frappe.defaults.get_user_default("Company")
		or frappe.db.get_single_value("Global Defaults", "default_company")
	)
	if not company or not frappe.db.exists("Company", company):
		return []

	values = frappe.db.get_value(
		"Company", company, ["company_logo", "tax_id", "phone_no", "email"], as_dict=True
	)
	company_form = ["Form", "Company", company]
	items = []

	if not values.company_logo and not frappe.db.exists("Letter Head", {"is_default": 1, "disabled": 0}):
		items.append(_item("logo", "Upload your logo", "Company > Company Logo. It prints on invoices, quotations and payslips.", company_form))
	if not values.tax_id:
		items.append(_item("pin", "Add your KRA PIN", "Company > Tax ID. Required on a tax invoice.", company_form))
	if not _has_company_address(company):
		items.append(_item("address", "Add your business address", "Company > Address & Contact > New Address.", company_form))
	if not (values.phone_no or values.email):
		items.append(_item("contact", "Add a phone number and email", "Company > Phone No. and Email, printed under your name.", company_form))
	if not _payment_details(company):
		items.append(
			_item(
				"payment",
				"Tell customers how to pay you",
				"Your M-Pesa Paybill/Till or bank details, printed on invoices and quotations.",
				["Form", "Kenya Accounting Settings", company],
			)
		)
	return items


@frappe.whitelist()
def dismiss():
	frappe.defaults.set_user_default(DISMISSED_KEY, 1)


def _item(key, label, hint, route):
	return {"key": key, "label": label, "hint": hint, "route": route}


def _has_company_address(company) -> bool:
	return bool(
		frappe.db.exists(
			"Dynamic Link",
			{"parenttype": "Address", "link_doctype": "Company", "link_name": company},
		)
	)


def _payment_details(company) -> str:
	if not frappe.db.exists("Kenya Accounting Settings", company):
		return ""
	if not frappe.get_meta("Kenya Accounting Settings").has_field("payment_details"):
		return ""
	return frappe.db.get_value("Kenya Accounting Settings", company, "payment_details") or ""
