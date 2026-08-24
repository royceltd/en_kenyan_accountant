# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Idempotent creation of Kenya WHT Tax Withholding Categories.

Spec section 12: rates below are illustrative statutory configuration from KRA
guidance - validate current rates, thresholds and effective dates before
production use. They are deliberately editable and versioned (each is a dated
row in the category's `rates` table) rather than a single hardcoded number, so
a future Finance Act change is a new rate row, not a code change.

Tax Withholding Category has no `company` field on the document itself - it is
shared, with one row per company in its `accounts` child table pointing at
that company's WHT Payable account. So re-running setup for a second company
adds a row to the *same* category rather than creating a duplicate category.
"""

import frappe

# (category_name, rate %) - spec section 12.
WHT_CATEGORIES = [
	("KE WHT - Professional Fees - Resident", 5),
	("KE WHT - Consultancy/Agency - Resident", 5),
	("KE WHT - Contractual - Resident", 3),
]

# Deliberately broad so the rate covers historical and future transactions
# until an accountant tightens the window for a specific statutory change -
# see the module docstring on versioning.
DEFAULT_RATE_FROM_DATE = "2010-01-01"
DEFAULT_RATE_TO_DATE = "2099-12-31"


def create_wht_categories(company, wht_payable_account):
	"""Create (or find) the standard Kenya WHT categories, and make sure each one
	has an account row for `company`. Returns the category names.
	"""
	names = []
	for category_name, rate in WHT_CATEGORIES:
		names.append(_get_or_create_wht_category(category_name, rate, company, wht_payable_account))
	return names


def _get_or_create_wht_category(category_name, rate, company, account):
	if frappe.db.exists("Tax Withholding Category", category_name):
		doc = frappe.get_doc("Tax Withholding Category", category_name)
	else:
		doc = frappe.new_doc("Tax Withholding Category")
		# autoname is "Prompt" for this doctype - name must be set explicitly.
		doc.name = category_name
		doc.category_name = category_name
		doc.tax_deduction_basis = "Net Total"
		doc.append(
			"rates",
			{
				"from_date": DEFAULT_RATE_FROM_DATE,
				"to_date": DEFAULT_RATE_TO_DATE,
				"tax_withholding_rate": rate,
			},
		)

	_ensure_company_account_row(doc, company, account)
	doc.save(ignore_permissions=True)
	return doc.name


def _ensure_company_account_row(doc, company, account):
	for row in doc.accounts:
		if row.company == company:
			return  # already configured for this company - leave it as-is
	doc.append("accounts", {"company": company, "account": account})
