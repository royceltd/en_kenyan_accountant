# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from kenyan_accountant.setup.accounts import create_core_tax_accounts
from kenyan_accountant.setup.utils import TEST_COMPANY
from kenyan_accountant.setup.wht import (
	WHT_PAYABLE_CATEGORIES,
	WHT_RECEIVABLE_CATEGORIES,
	create_wht_categories,
)

class IntegrationTestWht(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_creates_both_payable_and_receivable_categories_with_distinct_accounts(self):
		"""Regression test for a real bug: reusing the payable-side category on a
		Sales Invoice posted the withheld amount to the payable account -- a
		liability that doesn't exist -- instead of the receivable it actually
		is. Each direction must route to its own account."""
		accounts = create_core_tax_accounts(TEST_COMPANY)
		create_wht_categories(TEST_COMPANY, accounts["wht_payable_account"], accounts["wht_receivable_account"])

		for category_name, rate in WHT_PAYABLE_CATEGORIES:
			cat = frappe.get_doc("Tax Withholding Category", category_name)
			self.assertEqual(cat.get_company_account(TEST_COMPANY), accounts["wht_payable_account"])
			self.assertEqual(cat.rates[0].tax_withholding_rate, rate)

		for category_name, rate in WHT_RECEIVABLE_CATEGORIES:
			cat = frappe.get_doc("Tax Withholding Category", category_name)
			self.assertEqual(cat.get_company_account(TEST_COMPANY), accounts["wht_receivable_account"])
			self.assertEqual(cat.rates[0].tax_withholding_rate, rate)

		# Confirms the two sets are genuinely distinct documents, not aliases.
		payable_names = {name for name, _ in WHT_PAYABLE_CATEGORIES}
		receivable_names = {name for name, _ in WHT_RECEIVABLE_CATEGORIES}
		self.assertEqual(payable_names & receivable_names, set())

	def test_omitting_receivable_account_skips_receivable_categories(self):
		accounts = create_core_tax_accounts(TEST_COMPANY)
		create_wht_categories(TEST_COMPANY, accounts["wht_payable_account"])

		receivable_name = WHT_RECEIVABLE_CATEGORIES[0][0]
		self.assertFalse(frappe.db.exists("Tax Withholding Category", receivable_name))
