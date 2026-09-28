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

import re

import frappe
from frappe import _
from frappe.utils import flt, fmt_money, today

WHT_TAX_TYPE = "WHT"
VAT_WITHHOLDING_TAX_TYPE = "VAT Withholding"
WITHHELD_FROM_US = "Withheld From Us (Customer)"

# Kenya's standard VAT rate, used only to show the "VAT not shown" figure: KRA's
# guidelines for appointed VAT withholding agents (Dec 2023, item 2) deem an
# invoice from a VAT-registered supplier that shows no VAT to be VAT-inclusive.
STANDARD_VAT_RATE = 16


def _get_settings(company):
	if frappe.db.exists("Kenya Accounting Settings", company):
		return frappe.get_cached_doc("Kenya Accounting Settings", company)
	return None


@frappe.whitelist()
def get_vat_withholding_base(company: str, references) -> dict:
	"""The value VAT Withholding is 2% of, for the invoices a Payment Entry settles.

	KRA withholds on the taxable value of taxable supplies, and not on zero-rated
	or exempt ones (Guidelines for Appointed VAT Withholding Agents, item 3). So
	per invoice this is the VAT-exclusive value of the lines that actually carry
	VAT, read from ERPNext's own Item Wise Tax Detail rows for the company's VAT
	accounts. Rate > 0 is the filter on purpose: ERPNext adds every line to a tax
	row's taxable amount, a 0% (zero-rated/exempt) line included
	(taxes_and_totals.py), so the tax row's net total alone would over-withhold.

	A partial payment gets the same share of the taxable value as it pays of the
	invoice. Amounts are in company currency (taxable_amount is base).

	`references` is the Payment Entry's references table (unsaved form rows).
	"""
	references = frappe.parse_json(references) if isinstance(references, str) else (references or [])
	settings = _get_settings(company)
	if not settings:
		frappe.throw(_("{0} has no Kenya Accounting Settings configured yet.").format(company))
	vat_accounts = {a for a in (settings.output_vat_account, settings.input_vat_account) if a}

	invoices, total, total_full = [], 0.0, 0.0
	for ref in references:
		doctype, name = ref.get("reference_doctype"), ref.get("reference_name")
		allocated = flt(ref.get("allocated_amount"))
		if doctype not in ("Sales Invoice", "Purchase Invoice") or not name or not allocated:
			continue
		invoice = frappe.get_doc(doctype, name)
		invoice.check_permission("read")

		vat_rows = {t.name for t in invoice.taxes if t.account_head in vat_accounts}
		taxable = flt(
			sum(
				flt(d.taxable_amount)
				for d in invoice.get("item_wise_tax_details") or []
				if d.tax_row in vat_rows and flt(d.rate) > 0
			),
			2,
		)
		invoice_total = flt(invoice.rounded_total) or flt(invoice.grand_total)
		share = min(allocated / invoice_total, 1) if invoice_total else 0
		base = flt(taxable * share, 2)
		# What settling the whole remaining balance would give. Typing the net cash
		# into a payment first makes erpnext shrink the allocation to that cash, so a
		# full settlement looks like a part payment; the dialog asks which it is.
		outstanding = max(flt(ref.get("outstanding_amount")), allocated)
		share_full = min(outstanding / invoice_total, 1) if invoice_total else 0
		row = {
			"doctype": doctype,
			"invoice": name,
			"taxable_value": taxable,
			"share": flt(share, 6),
			"base": base,
			"allocated": allocated,
			"outstanding": outstanding,
			"base_full": flt(taxable * share_full, 2),
		}
		if not taxable:
			# Could be a zero-rated/exempt supply (no WVAT) or a taxable one with the
			# VAT not shown (KRA: deemed VAT-inclusive). Only the user knows which.
			row["deemed_inclusive_base"] = flt(allocated * 100 / (100 + STANDARD_VAT_RATE), 2)
		invoices.append(row)
		total += base
		total_full += row["base_full"]

	return {
		"base": flt(total, 2),
		"base_full": flt(total_full, 2),
		"rate": flt(settings.vat_withholding_rate),
		"invoices": invoices,
	}


@frappe.whitelist()
def make_wht_claim_journal_entry(credits) -> str:
	"""Draft the Journal Entry that uses WHT credits against income tax: debit
	Income Tax Payable, credit WHT Receivable, for the selected credits.

	Only a draft: nothing posts until the user submits it. Submitting ticks
	Claimed on the credits and cancelling unticks them (sync_wht_claims), so the
	register and the ledger can't disagree. Refuses anything that isn't a WHT
	credit withheld from this company, or is already claimed, or has no
	certificate number (the certificate is the proof KRA accepts).
	"""
	names = frappe.parse_json(credits) if isinstance(credits, str) else (credits or [])
	if not names:
		frappe.throw(_("Select the WHT credits to claim."))
	docs = [frappe.get_doc("Withholding Tax Credit", name) for name in names]

	problems = []
	for d in docs:
		if d.direction != WITHHELD_FROM_US or d.tax_type != WHT_TAX_TYPE:
			problems.append(_("{0}: only WHT a customer withheld from you is claimed against income tax.").format(d.name))
		elif d.claimed:
			problems.append(_("{0}: already claimed.").format(d.name))
		elif not d.certificate_number:
			problems.append(_("{0}: enter the certificate number first.").format(d.name))
		elif d.claim_journal_entry and frappe.db.get_value("Journal Entry", d.claim_journal_entry, "docstatus") in (0, 1):
			problems.append(_("{0}: already in Journal Entry {1}.").format(d.name, d.claim_journal_entry))
	companies = {d.company for d in docs}
	if len(companies) > 1:
		problems.append(_("The selected credits belong to more than one company."))
	if problems:
		frappe.throw("<br>".join(problems), title=_("Can't claim these credits"))

	company = companies.pop()
	settings = _get_settings(company)
	if not settings or not settings.income_tax_payable_account:
		frappe.throw(_("Set the Income Tax Payable Account in Kenya Accounting Settings for {0} first.").format(company))

	# One credit line per WHT Receivable account the credits actually sit in.
	by_account = {}
	for d in docs:
		by_account[d.account] = by_account.get(d.account, 0) + flt(d.amount)
	total = flt(sum(by_account.values()), 2)

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.company = company
	je.posting_date = today()
	je.user_remark = _("WHT credits used against income tax: {0}").format(
		", ".join(f"{d.name} ({d.party}, certificate {d.certificate_number})" for d in docs)
	)
	je.append("accounts", {"account": settings.income_tax_payable_account, "debit_in_account_currency": total})
	for account, amount in by_account.items():
		je.append("accounts", {"account": account, "credit_in_account_currency": flt(amount, 2)})
	je.insert()

	for d in docs:
		d.db_set("claim_journal_entry", je.name)
	return je.name


def sync_wht_claims(doc, method=None):
	"""Journal Entry before_submit/on_cancel/on_trash: keep the credits a claim
	entry was drafted for in step with it."""
	credits = frappe.get_all(
		"Withholding Tax Credit", filters={"claim_journal_entry": doc.name}, fields=["name", "account", "amount"]
	)
	if not credits:
		return

	if method == "before_submit":
		# The draft can be edited; the credits it ticks must still add up to what
		# it takes out of WHT Receivable.
		accounts = {c.account for c in credits}
		credited = flt(sum(flt(a.credit) for a in doc.accounts if a.account in accounts), 2)
		expected = flt(sum(flt(c.amount) for c in credits), 2)
		if credited != expected:
			frappe.throw(
				_("This entry was drafted to claim WHT credits of {0}, but it credits {1} to WHT Receivable. Change it back, or delete it and claim again.").format(
					fmt_money(expected), fmt_money(credited)
				)
			)
		for c in credits:
			frappe.db.set_value("Withholding Tax Credit", c.name, "claimed", 1)
	elif method == "on_cancel":
		for c in credits:
			frappe.db.set_value("Withholding Tax Credit", c.name, {"claimed": 0, "claim_journal_entry": None})
	elif method == "on_trash":
		for c in credits:
			frappe.db.set_value("Withholding Tax Credit", c.name, "claim_journal_entry", None)


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
		frappe.throw(f"{company} has no Kenya Accounting Settings configured yet.")

	if direction == "payable":
		if not settings.is_vat_withholding_agent:
			frappe.throw(
				f"{company} is not configured as a KRA-Appointed VAT Withholding Agent "
				"in Kenya Accounting Settings."
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
			certificate_number=_certificate_from(row.description),
		)


def _certificate_from(description):
	"""The certificate number the Add WHT Withheld dialog writes into the
	deduction's description ("WHT withheld (certificate X)"), so the credit
	carries it without retyping."""
	match = re.search(r"\(certificate ([^)]+)\)", description or "")
	return match.group(1).strip() if match else None


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
