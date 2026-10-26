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
# Reduced rate (eTIMS band E). In law petroleum products are 16%; at the time of
# writing a presidential directive has them at 8% temporarily. Only the default
# for templates created from now on - the client owns them once created
# (ADR-023), so a rate change is an edit to the template, not a release.
REDUCED_VAT_RATE = 8

# Item-level VAT treatments. Set on an Item (Taxes table), an Item Tax Template
# replaces the invoice template's rate for that line's VAT accounts only, so one
# invoice can mix 16%, 8%, zero-rated and exempt lines. Zero-rated and exempt
# are separate templates, not one "0%": the VAT return reports them separately,
# and only zero-rated supplies keep the right to recover input VAT.
#
# Rates per account role: output (sales), input (claimable purchase VAT) and
# non_recoverable (purchase VAT the business can't claim). A role left out keeps
# the invoice template's own rate - and the non-recoverable row's is always 0%,
# so only the Non-Claimable templates ever post to it.
ITEM_TAX_TEMPLATES = {
	"KE Zero Rated": {"output": 0, "input": 0},
	"KE Exempt": {"output": 0, "input": 0},
	"KE Standard 16%": {"output": STANDARD_VAT_RATE, "input": STANDARD_VAT_RATE},
	"KE Reduced 8%": {"output": REDUCED_VAT_RATE, "input": REDUCED_VAT_RATE},
	# Passenger cars and minibuses, and their repairs and spares: VAT is
	# charged but the buyer can't claim it (VAT Act 2013 s.17(5)).
	"KE Non-Claimable 16%": {"output": STANDARD_VAT_RATE, "input": 0, "non_recoverable": STANDARD_VAT_RATE},
	"KE Non-Claimable 8%": {"output": REDUCED_VAT_RATE, "input": 0, "non_recoverable": REDUCED_VAT_RATE},
}

ROLE_ACCOUNT_FIELDS = {
	"output": "output_vat_account",
	"input": "input_vat_account",
	"non_recoverable": "non_recoverable_vat_account",
}

# (Kenya Accounting Settings field, doctype, title, rate, inclusive). Purchase
# templates also get the 0% Non-Recoverable VAT row - see _purchase_rows().
VAT_TEMPLATES = [
	("sales_vat_template", "Sales Taxes and Charges Template", "KE - Standard VAT 16% - Sales", STANDARD_VAT_RATE, 0),
	(
		"sales_vat_inclusive_template",
		"Sales Taxes and Charges Template",
		"KE - Standard VAT 16% Inclusive - Sales",
		STANDARD_VAT_RATE,
		1,
	),
	(
		"sales_reduced_vat_inclusive_template",
		"Sales Taxes and Charges Template",
		"KE - Reduced VAT 8% Inclusive - Sales",
		REDUCED_VAT_RATE,
		1,
	),
	(
		"purchase_vat_template",
		"Purchase Taxes and Charges Template",
		"KE - Standard VAT 16% - Purchase",
		STANDARD_VAT_RATE,
		0,
	),
	(
		"purchase_vat_inclusive_template",
		"Purchase Taxes and Charges Template",
		"KE - Standard VAT 16% Inclusive - Purchase",
		STANDARD_VAT_RATE,
		1,
	),
	(
		"purchase_reduced_vat_inclusive_template",
		"Purchase Taxes and Charges Template",
		"KE - Reduced VAT 8% Inclusive - Purchase",
		REDUCED_VAT_RATE,
		1,
	),
]


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
	equivalent {fieldname: account_name} mapping.

	Returns {fieldname: template_name}, keyed to match Kenya Accounting Settings.
	"""
	result = {}
	for fieldname, doctype, title, rate, inclusive in VAT_TEMPLATES:
		is_purchase = doctype == "Purchase Taxes and Charges Template"
		account = accounts["input_vat_account" if is_purchase else "output_vat_account"]
		rows = [_vat_row(account, rate, inclusive, title, is_purchase)]
		if is_purchase:
			rows += _purchase_rows(accounts, inclusive)
		result[fieldname] = _get_or_create_tax_template(doctype, company, title, rows)
	return result


def _vat_row(account_head, rate, inclusive, description, is_purchase):
	row = {
		"charge_type": "On Net Total",
		"account_head": account_head,
		"description": description,
		"rate": rate,
		"included_in_print_rate": inclusive,
	}
	if is_purchase:
		# "Total" (not "Valuation"): recoverable input VAT affects the invoice
		# total, not item cost/stock valuation - spec section 8.
		row["category"] = "Total"
		row["add_deduct_tax"] = "Add"
	return row


def _purchase_rows(accounts, inclusive):
	"""The Non-Recoverable VAT row every purchase template carries at 0%. It
	only takes a rate on lines whose Item Tax Template switches it on (the
	KE Non-Claimable ones), which also zero the Input VAT row for that line -
	so a passenger car's VAT is expensed, never claimed."""
	if not accounts.get("non_recoverable_vat_account"):
		return []
	return [
		_vat_row(
			accounts["non_recoverable_vat_account"],
			0,
			inclusive,
			"Non-Recoverable VAT (passenger vehicles etc.)",
			is_purchase=True,
		)
	]


def add_non_recoverable_row(company, accounts):
	"""For companies set up before the Non-Recoverable VAT row existed: add it
	to their purchase templates - but only to a template still exactly as we
	shipped it (one row, our Input VAT account). One the accountant has changed
	is theirs (ADR-023) and is left alone. Returns (updated, skipped) names."""
	updated, skipped = [], []
	for _fieldname, doctype, title, _rate, inclusive in VAT_TEMPLATES:
		if doctype != "Purchase Taxes and Charges Template":
			continue
		name = frappe.db.get_value(doctype, {"title": title, "company": company}, "name")
		if not name:
			continue
		doc = frappe.get_doc(doctype, name)
		accounts_used = [row.account_head for row in doc.taxes]
		if accounts.get("non_recoverable_vat_account") in accounts_used:
			continue
		if accounts_used != [accounts["input_vat_account"]]:
			skipped.append(name)
			continue
		for row in _purchase_rows(accounts, inclusive):
			doc.append("taxes", row)
		doc.save(ignore_permissions=True)
		updated.append(name)
	return updated, skipped


def create_item_tax_templates(company, accounts):
	"""Item Tax Templates for each VAT treatment (ITEM_TAX_TEMPLATES). Found by the
	2026-09-28 withholding review: without them a client selling zero-rated or
	exempt goods had nothing to pick on the item, so those lines were charged the
	invoice's 16%.

	Returns {title: template_name}. An existing template with the same title is
	reused untouched (ADR-023: the client owns it once created).
	"""
	result = {}
	for title, rates in ITEM_TAX_TEMPLATES.items():
		existing = frappe.db.get_value("Item Tax Template", {"title": title, "company": company}, "name")
		if not existing:
			taxes = [
				{"tax_type": accounts[ROLE_ACCOUNT_FIELDS[role]], "tax_rate": rate}
				for role, rate in rates.items()
				if accounts.get(ROLE_ACCOUNT_FIELDS[role])
			]
			existing = (
				frappe.get_doc({"doctype": "Item Tax Template", "title": title, "company": company, "taxes": taxes})
				.insert(ignore_permissions=True)
				.name
			)
		result[title] = existing
	return result


def disable_erpnext_default_kenya_tax_templates(company):
	"""ERPNext's own setup wizard, for any company whose country is Kenya, auto-
	creates a single "Kenya Tax" Sales/Purchase template sharing one flat "VAT"
	account for both directions (erpnext/setup/setup_wizard/data/country_wise_tax
	.json - not something this app controls). It has no Input/Output split, no
	WHT, no settlement account - and sitting next to our own KE - Standard VAT
	16% templates it just leaves the accountant with two competing "default"
	choices, exactly the confusion this app exists to prevent.

	We never delete it - its underlying "VAT" account may already have real
	transactions posted against it (spec section 24: no destructive deletion of
	tax configuration). Disabling it is enough to stop it being offered as a
	choice on new invoices; existing ones are untouched.

	Returns the names of any templates this disabled (empty if none were found
	or they were already disabled).
	"""
	disabled = []
	for doctype in ("Sales Taxes and Charges Template", "Purchase Taxes and Charges Template"):
		name = frappe.db.get_value(doctype, {"title": "Kenya Tax", "company": company}, "name")
		if name and not frappe.db.get_value(doctype, name, "disabled"):
			frappe.db.set_value(doctype, name, "disabled", 1)
			disabled.append(name)
	return disabled


def _get_or_create_tax_template(doctype, company, title, rows):
	existing = frappe.db.get_value(doctype, {"title": title, "company": company}, "name")
	if existing:
		return existing

	doc = frappe.get_doc({"doctype": doctype, "title": title, "company": company, "taxes": rows})
	doc.insert(ignore_permissions=True)
	return doc.name
