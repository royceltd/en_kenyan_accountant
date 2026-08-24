# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from kenyan_accountant.setup.accounts import CORE_TAX_ACCOUNTS
from kenyan_accountant.setup.utils import TEST_COMPANY

EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


class IntegrationTestKenyanAccountantSettings(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def _settings(self):
		return frappe.get_doc({"doctype": "Kenyan Accountant Settings", "company": TEST_COMPANY}).insert()

	def test_autonames_to_company_and_starts_not_started(self):
		doc = self._settings()
		self.assertEqual(doc.name, TEST_COMPANY)
		self.assertEqual(doc.setup_status, "Not Started")

	def test_run_setup_populates_accounts_templates_and_moves_to_draft(self):
		doc = self._settings()
		result = doc.run_setup()

		self.assertEqual(result["status"], "Draft Configuration")
		doc.reload()
		self.assertEqual(doc.setup_status, "Draft Configuration")
		for spec in CORE_TAX_ACCOUNTS:
			self.assertTrue(doc.get(spec["fieldname"]), f"{spec['fieldname']} was not set")
		self.assertTrue(doc.sales_vat_template)
		self.assertTrue(doc.purchase_vat_template)
		self.assertTrue(doc.purchase_vat_inclusive_template)

	def test_run_setup_is_idempotent(self):
		doc = self._settings()
		doc.run_setup()
		first = {f: doc.get(f) for f in ("input_vat_account", "sales_vat_template")}

		doc.reload()
		doc.run_setup()
		doc.reload()
		second = {f: doc.get(f) for f in ("input_vat_account", "sales_vat_template")}

		self.assertEqual(first, second)

	def test_run_setup_never_overwrites_an_accountant_chosen_account(self):
		"""Spec section 22: core tax accounts are app-created but accountant-owned
		- if the accountant already pointed a field at a specific account,
		re-running setup must leave it alone."""
		doc = self._settings()
		doc.run_setup()
		doc.reload()

		custom_account = frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": "Input VAT (Custom)",
				"company": TEST_COMPANY,
				"parent_account": frappe.db.get_value(
					"Account", {"account_name": "Tax Assets", "company": TEST_COMPANY}, "name"
				),
				"account_type": "Tax",
				"root_type": "Asset",
			}
		).insert()
		doc.input_vat_account = custom_account.name
		doc.save()

		doc.run_setup()
		doc.reload()
		self.assertEqual(doc.input_vat_account, custom_account.name)

	def test_activate_is_blocked_until_reviewed(self):
		doc = self._settings()
		doc.run_setup()
		doc.reload()

		self.assertRaises(frappe.ValidationError, doc.activate)

	def test_ticking_reviewed_then_activate_moves_through_full_lifecycle(self):
		doc = self._settings()
		doc.run_setup()
		doc.reload()

		doc.reviewed = 1
		doc.save()
		self.assertEqual(doc.setup_status, "Reviewed")

		doc.activate()
		doc.reload()
		self.assertEqual(doc.setup_status, "Activated")

	def test_unticking_reviewed_demotes_back_to_draft(self):
		doc = self._settings()
		doc.run_setup()
		doc.reload()

		doc.reviewed = 1
		doc.save()
		self.assertEqual(doc.setup_status, "Reviewed")

		doc.reviewed = 0
		doc.save()
		self.assertEqual(doc.setup_status, "Draft Configuration")
