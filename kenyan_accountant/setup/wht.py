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
that company's WHT account. So re-running setup for a second company adds a
row to the *same* category rather than creating a duplicate category.

Two parallel sets of categories, not one, and this is deliberate: a Tax
Withholding Category has exactly one configured account per company
(Tax Withholding Category.validate_companies_and_accounts() rejects a second
row for the same company) - it cannot route to different accounts depending
on which direction the withholding runs. The *_PAYABLE categories are for
Purchase Invoice (this company withholding from a supplier), pointed at
wht_payable_account. The *_RECEIVABLE categories are for Sales Invoice (a
customer withholding from this company), pointed at wht_receivable_account.

Found for real, not assumed: the first version of sales-side WHT support
reused the purchase-side categories directly, since they carry the correct
rates. That silently posted every customer-withheld WHT amount to the
*payable* account -- a real, wrong liability entry for money nobody owes,
instead of the receivable/credit it actually is -- confirmed by checking the
actual GL Entries on a real submitted Sales Invoice, not assumed from the
category name alone.
"""

import frappe

# (category_name, rate %) - spec section 12.
WHT_PAYABLE_CATEGORIES = [
	("KE WHT - Professional Fees - Resident", 5),
	("KE WHT - Consultancy/Agency - Resident", 5),
	("KE WHT - Contractual - Resident", 3),
]

# Same names/rates, suffixed -- see module docstring for why these must be
# separate category documents, not the same ones reused on Sales Invoice.
WHT_RECEIVABLE_CATEGORIES = [
	(f"{name} (Withheld From Us)", rate) for name, rate in WHT_PAYABLE_CATEGORIES
]

# Deliberately broad so the rate covers historical and future transactions
# until an accountant tightens the window for a specific statutory change -
# see the module docstring on versioning.
DEFAULT_RATE_FROM_DATE = "2010-01-01"
DEFAULT_RATE_TO_DATE = "2099-12-31"


def create_wht_categories(company, wht_payable_account, wht_receivable_account=None):
	"""Create (or find) the standard Kenya WHT categories (both directions --
	see module docstring) and make sure each one has an account row for
	`company`. Returns the payable-side category names, unchanged, so existing
	callers keep working; receivable categories are created as a side effect.

	wht_receivable_account is optional only for backward compatibility with
	any caller that hasn't been updated to pass it yet -- when omitted, the
	receivable-side categories are simply skipped rather than created without
	an account, which Tax Withholding Category's own validation would reject
	anyway.
	"""
	names = []
	for category_name, rate in WHT_PAYABLE_CATEGORIES:
		names.append(_get_or_create_wht_category(category_name, rate, company, wht_payable_account))

	if wht_receivable_account:
		for category_name, rate in WHT_RECEIVABLE_CATEGORIES:
			_get_or_create_wht_category(category_name, rate, company, wht_receivable_account)

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
