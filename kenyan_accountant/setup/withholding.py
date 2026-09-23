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

- WHT withheld BY this company from a supplier keeps using ERPNext's own Tax
  Withholding Category engine exactly as it already did
  (kenyan_accountant.setup.wht's categories, unchanged), on Purchase Invoice.
- Everything withheld FROM this company (whether WHT or VAT Withholding, by a
  customer e.g. a parastatal), and everything this company withholds from a
  supplier for VAT specifically, is posted via Payment Entry's own native
  `deductions` table (account + amount) instead of forcing it through a
  category engine. Because it's a separate table, a Payment Entry can carry a
  WHT-driven invoice adjustment AND a VAT Withholding deduction at the same
  time -- that's what actually makes "both at once" possible.

A real, wrong design was tried and reverted before this: ERPNext's Sales
Invoice also has an `apply_tds`/tax_withholding_category mechanism that looks
identical to Purchase Invoice's, and the first version of this module reused
it to represent "a customer withheld WHT from us." Confirmed wrong by actually
submitting a real Sales Invoice and reading the resulting GL Entries: that
mechanism is ERPNext's `SalesTaxWithholding` controller, whose own docstring
says "(TCS)" -- Tax Collected at Source, an Indian regime where the *seller*
collects an *additional* tax from the buyer, structurally the opposite of a
customer withholding tax from what they owe the seller. It posted the "WHT
Receivable" account as a *credit* (correct for a TCS liability, wrong for a
receivable asset) and left the books unbalanced. Kenya has no TCS-equivalent
tax, so there was never a legitimate use for that mechanism here -- WHT
withheld from this company is handled the same way as VAT Withheld from this
company: a Payment Entry deduction against wht_receivable_account, entered
with whatever amount the withholding certificate actually says (WHT rates
vary by payment type, so unlike the flat 2% VAT rate, there's no single rate
to compute this from automatically).

Both mechanisms feed the same tracking doctype, Withholding Tax Credit, so an
accountant has one place to see every withholding credit -- who withheld it,
how much, whether a certificate number has been recorded, whether it's been
claimed on a return yet -- regardless of which mechanism produced it.
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
	to hardcode the rate or account. WHT has no equivalent helper -- its rate
	depends on the payment type (5% professional/consultancy, 3% contractual,
	...), not a single flat percentage, so an accountant enters the amount
	straight from the withholding certificate instead.

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


def sync_payment_withholding_credits(doc, method=None):
	"""Payment Entry on_submit/on_cancel/on_trash. Reads the standard
	`deductions` table (no Kenya-specific field added to it) and turns any row
	posted to one of this company's three withholding accounts into a tracked
	credit -- VAT Withholding Payable (this company withholding from a
	supplier), VAT Withholding Receivable or WHT Receivable (a customer
	withholding from this company). wht_payable_account is deliberately not
	included here: WHT this company withholds from a supplier is still handled
	entirely by ERPNext's own Tax Withholding Category engine on Purchase
	Invoice (see sync_wht_credits), not through this table.

	Idempotent by construction: on_cancel/on_trash simply wipe and, for a
	resubmit, on_submit always starts from a clean slate for this Payment
	Entry rather than trying to diff against what's already there.
	"""
	_clear_credits_for(doc.name)
	if method in ("on_cancel", "on_trash") or doc.docstatus != 1:
		return

	settings = _get_settings(doc.company)
	if not settings:
		return

	# account -> (direction, expected party_type, tax_type)
	account_map = {
		settings.vat_withholding_payable_account: (
			"Withheld By Us (Agent)", "Supplier", VAT_WITHHOLDING_TAX_TYPE,
		),
		settings.vat_withholding_receivable_account: (
			"Withheld From Us (Customer)", "Customer", VAT_WITHHOLDING_TAX_TYPE,
		),
		settings.wht_receivable_account: (
			"Withheld From Us (Customer)", "Customer", WHT_TAX_TYPE,
		),
	}
	account_map.pop(None, None)
	if not account_map:
		return

	for row in doc.deductions:
		if row.account not in account_map or not row.amount:
			continue
		direction, expected_party_type, tax_type = account_map[row.account]
		if doc.party_type != expected_party_type:
			# A payable-direction deduction only makes sense on a payment to a
			# Supplier, a receivable-direction one only on a payment from a
			# Customer -- an account picked for the wrong payment direction is
			# a real accountant mistake, not something to silently paper over.
			frappe.throw(
				f"Deduction row against {row.account} doesn't match this payment's "
				f"party type ({doc.party_type}). Expected {expected_party_type}."
			)
		_create_credit(
			company=doc.company,
			direction=direction,
			tax_type=tax_type,
			party_type=doc.party_type,
			party=doc.party,
			amount=abs(row.amount),
			account=row.account,
			payment_entry=doc.name,
			remarks=row.description,
		)


def sync_wht_credits(doc, method=None):
	"""Purchase Invoice on_submit/on_cancel only -- this company withholding
	WHT from a supplier, via ERPNext's own, unmodified Tax Withholding
	Category/Entry engine (kenyan_accountant.setup.wht's categories). There is
	no Sales Invoice equivalent -- see the module docstring for why that was
	tried and reverted.
	"""
	_clear_credits_for_invoice(doc.doctype, doc.name)
	if method == "on_cancel" or doc.docstatus != 1:
		return

	entries = [e for e in (doc.get("tax_withholding_entries") or []) if e.withholding_amount]
	if not entries:
		return

	for entry in entries:
		account = _wht_account_for(entry.tax_withholding_category, doc.company)
		_create_credit(
			company=doc.company,
			direction="Withheld By Us (Agent)",
			tax_type=WHT_TAX_TYPE,
			party_type=entry.party_type,
			party=entry.party,
			amount=abs(entry.withholding_amount),
			account=account,
			# No Payment Entry involved -- ERPNext's own engine computes and
			# posts WHT at Purchase Invoice submission, not at payment time.
			# reference_invoice is this record's real source instead.
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
