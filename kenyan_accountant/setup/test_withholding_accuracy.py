# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""v16.3.0: the VAT Withholding base from the invoices a payment settles, the
Income Tax Payable account, and Claim WHT Credits.

Invoices here are placeholder rows written with db_insert() (same approach and
reasons as test_withholding.py): get_vat_withholding_base() only reads an
invoice's totals, its taxes rows and its Item Wise Tax Detail rows, so those are
all a test invoice needs. The Journal Entries are real, inserted and submitted,
because the claim's whole point is what posts to the ledger.
"""

import frappe
from frappe.tests import IntegrationTestCase

from kenyan_accountant.patches.v16_3 import add_income_tax_payable_account
from kenyan_accountant.setup.accounts import create_core_tax_accounts
from kenyan_accountant.setup.utils import TEST_COMPANY
from kenyan_accountant.setup.withholding import (
	_certificate_from,
	_create_credit,
	get_vat_withholding_base,
	make_wht_claim_journal_entry,
)


class IntegrationTestWithholdingAccuracy(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def _settings(self, **overrides):
		accounts = create_core_tax_accounts(TEST_COMPANY)
		return frappe.get_doc(
			{
				"doctype": "Kenya Accounting Settings",
				"company": TEST_COMPANY,
				"is_vat_withholding_agent": 1,
				"vat_withholding_rate": 2,
				**accounts,
				**overrides,
			}
		).insert()

	def _fake_party(self, party_type, name):
		if frappe.db.exists(party_type, name):
			return
		fieldname = "supplier_name" if party_type == "Supplier" else "customer_name"
		frappe.get_doc({"doctype": party_type, "name": name, fieldname: name}).db_insert()

	def _fake_invoice(self, name, vat_account, lines, grand_total):
		"""lines: [(vat_rate, taxable_amount)] per item line."""
		self._fake_party("Customer", "_Test Customer")
		tax_row = f"{name}-vat"
		doc = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"name": name,
				"company": TEST_COMPANY,
				"customer": "_Test Customer",
				"grand_total": grand_total,
				"rounded_total": grand_total,
				"taxes": [{"name": tax_row, "account_head": vat_account, "charge_type": "On Net Total", "rate": 16}]
				if vat_account
				else [],
				"item_wise_tax_details": [
					{"tax_row": tax_row, "item_row": f"{name}-item-{i}", "rate": rate, "taxable_amount": amount}
					for i, (rate, amount) in enumerate(lines)
				]
				if vat_account
				else [],
			}
		)
		doc.db_insert()
		for child in doc.get_all_children():
			child.db_insert()

	def _ref(self, name, allocated, outstanding=None):
		return {
			"reference_doctype": "Sales Invoice",
			"reference_name": name,
			"allocated_amount": allocated,
			"outstanding_amount": outstanding if outstanding is not None else allocated,
		}

	# --- VAT Withholding base -------------------------------------------------

	def test_base_is_vat_exclusive_value_of_standard_rated_lines(self):
		settings = self._settings()
		self._fake_invoice("_T-SINV-STD", settings.output_vat_account, [(16, 100000)], 116000)
		result = get_vat_withholding_base(TEST_COMPANY, [self._ref("_T-SINV-STD", 116000)])
		self.assertEqual(result["base"], 100000)
		self.assertEqual(result["rate"], 2)

	def test_base_leaves_out_zero_rated_lines(self):
		settings = self._settings()
		# 60,000 standard-rated + 40,000 zero-rated: VAT 9,600, total 109,600.
		self._fake_invoice("_T-SINV-MIX", settings.output_vat_account, [(16, 60000), (0, 40000)], 109600)
		result = get_vat_withholding_base(TEST_COMPANY, [self._ref("_T-SINV-MIX", 109600)])
		self.assertEqual(result["base"], 60000)

	def test_base_scales_with_a_partial_payment(self):
		settings = self._settings()
		self._fake_invoice("_T-SINV-PART", settings.output_vat_account, [(16, 100000)], 116000)
		result = get_vat_withholding_base(TEST_COMPANY, [self._ref("_T-SINV-PART", 58000)])
		self.assertEqual(result["base"], 50000)
		self.assertEqual(result["invoices"][0]["share"], 0.5)

	def test_net_cash_typed_first_offers_the_whole_balance(self):
		"""Typing the net cash (83,250) makes erpnext allocate only that much of an
		87,000 invoice. The base for that allocation is a part-payment figure; the
		whole-balance figure is what a full settlement with tax withheld needs."""
		settings = self._settings()
		self._fake_invoice("_T-SINV-NET", settings.output_vat_account, [(16, 75000)], 87000)
		result = get_vat_withholding_base(TEST_COMPANY, [self._ref("_T-SINV-NET", 83250, outstanding=87000)])
		self.assertEqual(result["base_full"], 75000)
		self.assertAlmostEqual(result["base"], 71767.24, places=2)

	def test_base_adds_up_several_invoices(self):
		settings = self._settings()
		self._fake_invoice("_T-SINV-A", settings.output_vat_account, [(16, 100000)], 116000)
		self._fake_invoice("_T-SINV-B", settings.output_vat_account, [(16, 25000)], 29000)
		result = get_vat_withholding_base(
			TEST_COMPANY, [self._ref("_T-SINV-A", 116000), self._ref("_T-SINV-B", 29000)]
		)
		self.assertEqual(result["base"], 125000)

	def test_invoice_without_vat_gives_zero_and_the_deemed_inclusive_figure(self):
		self._settings()
		self._fake_invoice("_T-SINV-NOVAT", None, [], 116000)
		result = get_vat_withholding_base(TEST_COMPANY, [self._ref("_T-SINV-NOVAT", 116000)])
		self.assertEqual(result["base"], 0)
		self.assertEqual(result["invoices"][0]["deemed_inclusive_base"], 100000)

	def test_base_ignores_other_tax_accounts(self):
		settings = self._settings()
		# A tax row that isn't one of the company's VAT accounts (e.g. a levy) doesn't count.
		self._fake_invoice("_T-SINV-OTHER", settings.wht_payable_account, [(16, 100000)], 116000)
		result = get_vat_withholding_base(TEST_COMPANY, [self._ref("_T-SINV-OTHER", 116000)])
		self.assertEqual(result["base"], 0)

	# --- Income Tax Payable ---------------------------------------------------

	def test_setup_creates_income_tax_payable(self):
		accounts = create_core_tax_accounts(TEST_COMPANY)
		account = frappe.get_doc("Account", accounts["income_tax_payable_account"])
		self.assertEqual(account.account_name, "Income Tax Payable")
		self.assertEqual(account.root_type, "Liability")
		self.assertFalse(account.balance_must_be)

	def test_patch_fills_an_empty_setting_only(self):
		settings = self._settings(income_tax_payable_account=None)
		add_income_tax_payable_account.execute()
		filled = frappe.db.get_value("Kenya Accounting Settings", settings.name, "income_tax_payable_account")
		self.assertTrue(filled)

		frappe.db.set_value("Kenya Accounting Settings", settings.name, "income_tax_payable_account", settings.wht_payable_account)
		add_income_tax_payable_account.execute()
		kept = frappe.db.get_value("Kenya Accounting Settings", settings.name, "income_tax_payable_account")
		self.assertEqual(kept, settings.wht_payable_account)

	# --- Claim WHT Credits ----------------------------------------------------

	def _credit(self, settings, tax_type="WHT", amount=2250, certificate="KRA-WHT-TEST-1"):
		self._fake_party("Customer", "_Test Customer")
		account = settings.wht_receivable_account if tax_type == "WHT" else settings.vat_withholding_receivable_account
		_create_credit(
			company=TEST_COMPANY,
			direction="Withheld From Us (Customer)",
			tax_type=tax_type,
			party_type="Customer",
			party="_Test Customer",
			amount=amount,
			account=account,
			certificate_number=certificate,
		)
		return frappe.get_last_doc("Withholding Tax Credit")

	def test_claim_drafts_the_journal_entry(self):
		settings = self._settings()
		a = self._credit(settings, amount=2250, certificate="C-1")
		b = self._credit(settings, amount=1000, certificate="C-2")
		je = frappe.get_doc("Journal Entry", make_wht_claim_journal_entry([a.name, b.name]))

		self.assertEqual(je.docstatus, 0)
		debit = {r.account: r.debit_in_account_currency for r in je.accounts if r.debit_in_account_currency}
		credit = {r.account: r.credit_in_account_currency for r in je.accounts if r.credit_in_account_currency}
		self.assertEqual(debit, {settings.income_tax_payable_account: 3250})
		self.assertEqual(credit, {settings.wht_receivable_account: 3250})
		self.assertIn("C-1", je.user_remark)
		self.assertEqual(frappe.db.get_value("Withholding Tax Credit", a.name, "claim_journal_entry"), je.name)
		self.assertFalse(frappe.db.get_value("Withholding Tax Credit", a.name, "claimed"))

	def test_submit_ticks_claimed_and_cancel_unticks(self):
		settings = self._settings()
		a = self._credit(settings)
		je = frappe.get_doc("Journal Entry", make_wht_claim_journal_entry([a.name]))
		je.submit()
		self.assertEqual(frappe.db.get_value("Withholding Tax Credit", a.name, "claimed"), 1)

		je.cancel()
		row = frappe.db.get_value("Withholding Tax Credit", a.name, ["claimed", "claim_journal_entry"], as_dict=True)
		self.assertEqual(row.claimed, 0)
		self.assertFalse(row.claim_journal_entry)

	def test_submit_refuses_an_edited_amount(self):
		settings = self._settings()
		a = self._credit(settings, amount=2250)
		je = frappe.get_doc("Journal Entry", make_wht_claim_journal_entry([a.name]))
		for row in je.accounts:
			row.debit_in_account_currency = row.debit_in_account_currency and 2000
			row.credit_in_account_currency = row.credit_in_account_currency and 2000
		je.save()
		self.assertRaises(frappe.ValidationError, je.submit)

	def test_deleting_the_draft_frees_the_credits(self):
		settings = self._settings()
		a = self._credit(settings)
		name = make_wht_claim_journal_entry([a.name])
		frappe.delete_doc("Journal Entry", name)
		self.assertFalse(frappe.db.get_value("Withholding Tax Credit", a.name, "claim_journal_entry"))
		make_wht_claim_journal_entry([a.name])  # can be claimed again

	def test_claim_refuses_vat_credits_uncertified_and_already_claimed(self):
		settings = self._settings()
		wvat = self._credit(settings, tax_type="VAT Withholding", amount=1500)
		no_cert = self._credit(settings, certificate=None)
		claimed = self._credit(settings, certificate="C-9")
		frappe.db.set_value("Withholding Tax Credit", claimed.name, "claimed", 1)
		for name in (wvat.name, no_cert.name, claimed.name):
			self.assertRaises(frappe.ValidationError, make_wht_claim_journal_entry, [name])

	def test_claim_needs_the_income_tax_account(self):
		settings = self._settings(income_tax_payable_account=None)
		frappe.clear_document_cache("Kenya Accounting Settings", settings.name)
		a = self._credit(settings)
		self.assertRaises(frappe.ValidationError, make_wht_claim_journal_entry, [a.name])

	def test_certificate_number_is_read_from_the_deduction_description(self):
		self.assertEqual(_certificate_from("WHT withheld (certificate KRA-WHT-NCC-55102)"), "KRA-WHT-NCC-55102")
		self.assertIsNone(_certificate_from("WHT withheld"))
		self.assertIsNone(_certificate_from(None))
