# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from kenyan_accountant.setup.accounts import create_core_tax_accounts
from kenyan_accountant.setup.utils import TEST_COMPANY
from kenyan_accountant.setup.withholding import (
	compute_vat_withholding_amount,
	sync_vat_withholding_credits,
)

# sync_vat_withholding_credits only reads specific fields off its `doc` argument
# and never calls doc.save() -- a hand-built frappe._dict exercises the real
# logic (account matching, party-type mismatch guard, credit creation/removal)
# without needing a fully GL-balanced, submittable Payment Entry, which needs a
# real bank account and mode of payment this app has no reason to own the setup
# of. The full, real accounting-engine path (an actual submitted Payment Entry
# with a deduction row added via the shipped Client Script) was verified live
# against demo.royceerp.com instead -- see the Sep 2026 session notes.
#
# Withholding Tax Credit's own party/payment_entry fields are real Links,
# though (correctly -- production always has a genuine submitted Payment Entry
# by the time this runs), so a synthetic doc still needs *something* real for
# those two to point at. _fake_payment_entry() writes a bare placeholder row
# via db_insert() (skips full Payment Entry business validation, which needs a
# real bank account this test has no reason to set up) purely to satisfy Link
# validation -- rolled back in tearDown like everything else here.

EXTRA_TEST_RECORD_DEPENDENCIES = ["Supplier"]


class IntegrationTestWithholding(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def _settings(self, is_agent=1, rate=2):
		accounts = create_core_tax_accounts(TEST_COMPANY)
		doc = frappe.get_doc(
			{
				"doctype": "Kenyan Accountant Settings",
				"company": TEST_COMPANY,
				"is_vat_withholding_agent": is_agent,
				"vat_withholding_rate": rate,
				**accounts,
			}
		).insert()
		return doc

	def _fake_payment_entry(self, name):
		frappe.get_doc(
			{
				"doctype": "Payment Entry",
				"name": name,
				"company": TEST_COMPANY,
				"party_type": "Supplier",
				"party": "_Test Supplier",
			}
		).db_insert()

	def test_compute_payable_requires_agent_status(self):
		self._settings(is_agent=0)
		self.assertRaises(
			frappe.ValidationError,
			compute_vat_withholding_amount,
			TEST_COMPANY, 100000, "payable",
		)

	def test_compute_receivable_does_not_require_agent_status(self):
		"""A customer withholding from us has nothing to do with whether *we*
		are a gazetted agent -- that flag must only gate the payable side."""
		self._settings(is_agent=0)
		result = compute_vat_withholding_amount(TEST_COMPANY, 100000, "receivable")
		self.assertEqual(result["amount"], 2000)

	def test_compute_amount_uses_configured_rate(self):
		self._settings(is_agent=1, rate=2)
		result = compute_vat_withholding_amount(TEST_COMPANY, 50000, "payable")
		self.assertEqual(result["rate"], 2)
		self.assertEqual(result["amount"], 1000)

	def test_compute_rejects_unknown_direction(self):
		self._settings()
		self.assertRaises(
			frappe.ValidationError,
			compute_vat_withholding_amount,
			TEST_COMPANY, 100000, "sideways",
		)

	def test_sync_creates_credit_from_matching_payable_deduction(self):
		settings = self._settings()
		self._fake_payment_entry("_TEST-PE-0001")
		pe = frappe._dict(
			doctype="Payment Entry",
			name="_TEST-PE-0001",
			company=TEST_COMPANY,
			party_type="Supplier",
			party="_Test Supplier",
			docstatus=1,
			deductions=[
				frappe._dict(
					account=settings.vat_withholding_payable_account,
					amount=1000,
					description="VAT Withholding (2%)",
				)
			],
		)
		sync_vat_withholding_credits(pe, "on_submit")

		credit = frappe.get_doc("Withholding Tax Credit", {"payment_entry": "_TEST-PE-0001"})
		self.assertEqual(credit.tax_type, "VAT Withholding")
		self.assertEqual(credit.direction, "Withheld By Us (Agent)")
		self.assertEqual(credit.party_type, "Supplier")
		self.assertEqual(credit.amount, 1000)

	def test_sync_ignores_deductions_against_unrelated_accounts(self):
		self._settings()
		pe = frappe._dict(
			doctype="Payment Entry",
			name="_TEST-PE-0002",
			company=TEST_COMPANY,
			party_type="Supplier",
			party="_Test Supplier",
			docstatus=1,
			deductions=[frappe._dict(account="Bank Charges - KATC", amount=50, description="Bank fee")],
		)
		sync_vat_withholding_credits(pe, "on_submit")
		self.assertFalse(frappe.db.exists("Withholding Tax Credit", {"payment_entry": "_TEST-PE-0002"}))

	def test_sync_rejects_account_party_type_mismatch(self):
		"""A VAT Withholding Payable deduction only makes sense on a payment to a
		Supplier -- picking it on a payment from a Customer is a real mistake."""
		settings = self._settings()
		pe = frappe._dict(
			doctype="Payment Entry",
			name="_TEST-PE-0003",
			company=TEST_COMPANY,
			party_type="Customer",
			party="_Test Customer",
			docstatus=1,
			deductions=[
				frappe._dict(account=settings.vat_withholding_payable_account, amount=1000, description="")
			],
		)
		self.assertRaises(frappe.ValidationError, sync_vat_withholding_credits, pe, "on_submit")

	def test_sync_on_cancel_removes_existing_credit(self):
		settings = self._settings()
		self._fake_payment_entry("_TEST-PE-0004")
		pe = frappe._dict(
			doctype="Payment Entry",
			name="_TEST-PE-0004",
			company=TEST_COMPANY,
			party_type="Supplier",
			party="_Test Supplier",
			docstatus=1,
			deductions=[
				frappe._dict(account=settings.vat_withholding_payable_account, amount=1000, description="")
			],
		)
		sync_vat_withholding_credits(pe, "on_submit")
		self.assertTrue(frappe.db.exists("Withholding Tax Credit", {"payment_entry": "_TEST-PE-0004"}))

		pe.docstatus = 2
		sync_vat_withholding_credits(pe, "on_cancel")
		self.assertFalse(frappe.db.exists("Withholding Tax Credit", {"payment_entry": "_TEST-PE-0004"}))
