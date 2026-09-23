# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""WHT and VAT Withholding (WVAT) can apply to the same invoice at once -- a
KRA-gazetted parastatal paying a VAT-registered consultant for professional
services owes both a 5% WHT (Income Tax Act) and a 2% WVAT (VAT Act),
independently, on the same taxable value. Researched directly against
ERPNext's own v16 source before building this: `Tax Withholding Category`
structurally cannot represent both in one place (one account per company per
category, one rate per date/group -- see validate_companies_and_accounts()
and validate_dates() in erpnext's own tax_withholding_category.py), and
Purchase/Sales Invoice Item's tax_withholding_category is a single Link, so
one line can only ever carry one category through that engine.

So this module deliberately does NOT try to extend that engine for VAT
withholding. Two separate mechanisms, running side by side:

- WHT keeps using ERPNext's own Tax Withholding Category/Entry engine exactly
  as it already does (kenyan_accountant.setup.wht's categories, unchanged) --
  on both the purchase side (already live) and, newly usable here, the sales
  side (Customer.tax_withholding_category + apply_tds), which is what makes a
  customer withholding WHT from us representable at all. wht_receivable_account
  already existed in Kenyan Accountant Settings for exactly this before any
  code used it.
- VAT Withholding is new and independent: a flat rate, a company-level "are we
  even an agent" gate, and posted via Payment Entry's own native `deductions`
  table (account + amount, a mechanism ERPNext already ships for "less cash
  moved than the invoice said, and it's not a discount") rather than forcing
  it through the category engine. Because it's a separate table, a Payment
  Entry can carry a WHT-driven invoice adjustment AND a VAT Withholding
  deduction at the same time -- that's what actually makes "both at once"
  possible without patching ERPNext core.

Both mechanisms feed the same tracking doctype, Withholding Tax Credit, so an
accountant has one place to see every withholding credit -- who withheld it,
how much, whether a certificate number has been recorded, whether it's been
claimed on a return yet -- regardless of which of the two mechanisms produced
it.
"""

import frappe

WHT_TAX_TYPE = "WHT"
VAT_WITHHOLDING_TAX_TYPE = "VAT Withholding"


def _get_settings(company):
	if frappe.db.exists("Kenyan Accountant Settings", company):
		return frappe.get_cached_doc("Kenyan Accountant Settings", company)
	return None


@frappe.whitelist()
def compute_vat_withholding_amount(company: str, taxable_amount: float, direction: str) -> dict:
	"""Rate/amount for a VAT Withholding deduction, for callers (the Payment
	Entry client script, or an accountant computing it by hand) that don't want
	to hardcode the rate or account.

	`direction` matters: "payable" (this company withholding from a supplier)
	is only lawful once this company is itself a gazetted agent, and is gated
	on that flag. "receivable" (a customer, e.g. a parastatal, withholding from
	this company) is not this company's authorization to grant or withhold at
	all -- it's a fact about the customer, not us -- so that flag is
	deliberately not checked on this path. Both directions share the same
	statutory rate field: it's one KRA-set percentage, not something either
	party negotiates.
	"""
	settings = _get_settings(company)
	if not settings:
		frappe.throw(f"{company} has no Kenyan Accountant Settings configured yet.")

	if direction == "payable":
		if not settings.is_vat_withholding_agent:
			frappe.throw(
				f"{company} is not configured as a KRA-Appointed VAT Withholding Agent "
				"in Kenyan Accountant Settings."
			)
		account = settings.vat_withholding_payable_account
	elif direction == "receivable":
		account = settings.vat_withholding_receivable_account
	else:
		frappe.throw(f"Unknown direction '{direction}' -- expected 'payable' or 'receivable'.")

	if not account:
		frappe.throw(f"No VAT Withholding account configured for {company} for this direction yet.")

	rate = settings.vat_withholding_rate or 0
	amount = round(float(taxable_amount) * rate / 100, 2)
	return {"rate": rate, "amount": amount, "account": account}


def _clear_credits_for(payment_entry_name):
	frappe.db.delete("Withholding Tax Credit", {"payment_entry": payment_entry_name})


def _create_credit(**kwargs):
	frappe.get_doc({"doctype": "Withholding Tax Credit", **kwargs}).insert(ignore_permissions=True)


def sync_vat_withholding_credits(doc, method=None):
	"""Payment Entry on_submit/on_cancel/on_trash. Reads the standard
	`deductions` table (no Kenya-specific field added to it) and turns any row
	posted to one of this company's two VAT Withholding accounts into a
	tracked credit. Idempotent by construction: on_cancel/on_trash simply wipe
	and, for a resubmit, on_submit always starts from a clean slate for this
	Payment Entry rather than trying to diff against what's already there.
	"""
	_clear_credits_for(doc.name)
	if method in ("on_cancel", "on_trash") or doc.docstatus != 1:
		return

	settings = _get_settings(doc.company)
	if not settings:
		return

	account_directions = {
		settings.vat_withholding_payable_account: ("Withheld By Us (Agent)", "Supplier"),
		settings.vat_withholding_receivable_account: ("Withheld From Us (Customer)", "Customer"),
	}
	account_directions.pop(None, None)
	if not account_directions:
		return

	for row in doc.deductions:
		if row.account not in account_directions or not row.amount:
			continue
		direction, expected_party_type = account_directions[row.account]
		if doc.party_type != expected_party_type:
			# A VAT Withholding Payable deduction only makes sense on a payment
			# to a Supplier, a Receivable one only on a payment from a Customer --
			# an account picked for the wrong payment direction is a real
			# accountant mistake, not something to silently paper over.
			frappe.throw(
				f"Deduction row against {row.account} doesn't match this payment's "
				f"party type ({doc.party_type}). Expected {expected_party_type}."
			)
		_create_credit(
			company=doc.company,
			direction=direction,
			tax_type=VAT_WITHHOLDING_TAX_TYPE,
			party_type=doc.party_type,
			party=doc.party,
			amount=abs(row.amount),
			account=row.account,
			payment_entry=doc.name,
			remarks=row.description,
		)


def sync_wht_credits(doc, method=None):
	"""Purchase Invoice / Sales Invoice on_submit/on_cancel. Reads ERPNext's own
	`tax_withholding_entries` (populated by the existing, unmodified Tax
	Withholding Category engine) so WHT credits show up in the same tracking
	list as VAT Withholding ones, even though the two are computed by entirely
	different mechanisms. Purchase Invoice = we withheld (Direction: By Us);
	Sales Invoice = a customer withheld from us (Direction: From Us) -- the
	first real use of wht_receivable_account, which existed in settings before
	any code referenced it.
	"""
	_clear_credits_for_invoice(doc.doctype, doc.name)
	if method == "on_cancel" or doc.docstatus != 1:
		return

	entries = [e for e in (doc.get("tax_withholding_entries") or []) if e.withholding_amount]
	if not entries:
		return

	direction = "Withheld By Us (Agent)" if doc.doctype == "Purchase Invoice" else "Withheld From Us (Customer)"

	for entry in entries:
		account = _wht_account_for(entry.tax_withholding_category, doc.company)
		_create_credit(
			company=doc.company,
			direction=direction,
			tax_type=WHT_TAX_TYPE,
			party_type=entry.party_type,
			party=entry.party,
			amount=abs(entry.withholding_amount),
			account=account,
			# No Payment Entry involved here -- ERPNext's own engine computes and
			# posts WHT at Purchase/Sales Invoice submission, not at payment time,
			# unlike VAT Withholding. reference_invoice is this record's real
			# source instead; payment_entry is left unset (see its field
			# definition -- not required, on purpose, for exactly this case).
			reference_invoice_doctype=doc.doctype,
			reference_invoice=doc.name,
		)


def _wht_account_for(category_name, company):
	try:
		return frappe.get_cached_doc("Tax Withholding Category", category_name).get_company_account(company)
	except frappe.ValidationError:
		return None


def _clear_credits_for_invoice(doctype, name):
	frappe.db.delete(
		"Withholding Tax Credit",
		{"reference_invoice_doctype": doctype, "reference_invoice": name},
	)
