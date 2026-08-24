# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Idempotent creation of Kenya Tax Categories and VAT tax templates.

Spec sections 7-10. Tax Category has no `company` field - it is a global,
shared lookup, so it is created once regardless of how many companies run
setup. Sales/Purchase Taxes and Charges Templates are per-company.
"""

import frappe

# Section 10: a small, meaningful set of Tax Categories - one per treatment,
# not one per possible rate/rule combination.
TAX_CATEGORIES = [
	"KE Standard",
	"KE Zero Rated",
	"KE Exempt",
	"KE Non-Recoverable",
]

STANDARD_VAT_RATE = 16


def create_tax_categories():
	"""Create (or find) the standard Kenya Tax Categories. Returns their names."""
	names = []
	for title in TAX_CATEGORIES:
		if not frappe.db.exists("Tax Category", title):
			frappe.get_doc({"doctype": "Tax Category", "title": title}).insert(ignore_permissions=True)
		names.append(title)
	return names


def create_vat_templates(company, accounts):
	"""accounts: the dict returned by accounts.create_core_tax_accounts(), or an
	equivalent {fieldname: account_name} mapping - only input_vat_account and
	output_vat_account are used here.

	Returns {fieldname: template_name}, keyed to match Kenyan Accountant Settings.
	"""
	result = {}

	result["sales_vat_template"] = _get_or_create_tax_template(
		"Sales Taxes and Charges Template",
		company=company,
		title="KE - Standard VAT 16% - Sales",
		account_head=accounts["output_vat_account"],
		rate=STANDARD_VAT_RATE,
		description="Kenya Standard VAT 16% - Sales",
	)
	result["purchase_vat_template"] = _get_or_create_tax_template(
		"Purchase Taxes and Charges Template",
		company=company,
		title="KE - Standard VAT 16% - Purchase",
		account_head=accounts["input_vat_account"],
		rate=STANDARD_VAT_RATE,
		description="Kenya Standard VAT 16% - Purchases",
		is_purchase=True,
	)
	result["purchase_vat_inclusive_template"] = _get_or_create_tax_template(
		"Purchase Taxes and Charges Template",
		company=company,
		title="KE - Standard VAT 16% Inclusive - Purchase",
		account_head=accounts["input_vat_account"],
		rate=STANDARD_VAT_RATE,
		description="Kenya Standard VAT 16% Inclusive - Purchases",
		included_in_print_rate=1,
		is_purchase=True,
	)
	return result


def _get_or_create_tax_template(
	doctype, company, title, account_head, rate, description, included_in_print_rate=0, is_purchase=False
):
	existing = frappe.db.get_value(doctype, {"title": title, "company": company}, "name")
	if existing:
		return existing

	row = {
		"charge_type": "On Net Total",
		"account_head": account_head,
		"description": description,
		"rate": rate,
		"included_in_print_rate": included_in_print_rate,
	}
	if is_purchase:
		# "Total" (not "Valuation"): recoverable input VAT affects the invoice
		# total, not item cost/stock valuation - spec section 8.
		row["category"] = "Total"
		row["add_deduct_tax"] = "Add"

	doc = frappe.get_doc(
		{
			"doctype": doctype,
			"title": title,
			"company": company,
			"taxes": [row],
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name
