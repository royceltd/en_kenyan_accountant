# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Tests for the setup/ installer functions directly (not through the Kenyan
Accountant Settings doctype) - accounts.py, vat.py and wht.py each own one
concern from the spec and are tested against that concern in isolation.
See kenyan_accountant_settings/test_kenyan_accountant_settings.py for the
orchestration/lifecycle tests that go through the doctype.
"""

import frappe
from frappe.tests import IntegrationTestCase

from kenyan_accountant.setup.accounts import CORE_TAX_ACCOUNTS, create_core_tax_accounts
from kenyan_accountant.setup.utils import TEST_COMPANY
from kenyan_accountant.setup.vat import TAX_CATEGORIES, create_tax_categories, create_vat_templates
from kenyan_accountant.setup.wht import WHT_CATEGORIES, create_wht_categories


class TestCreateCoreTaxAccounts(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_creates_all_six_accounts_with_correct_nature(self):
		accounts = create_core_tax_accounts(TEST_COMPANY)

		self.assertEqual(set(accounts.keys()), {spec["fieldname"] for spec in CORE_TAX_ACCOUNTS})

		for spec in CORE_TAX_ACCOUNTS:
			account = frappe.get_doc("Account", accounts[spec["fieldname"]])
			self.assertEqual(account.company, TEST_COMPANY)
			self.assertEqual(account.account_type, "Tax")
			self.assertEqual(account.root_type, spec["root_type"])
			self.assertEqual(account.balance_must_be, spec["balance_must_be"])

	def test_reuses_existing_kenya_relevant_groups(self):
		"""Standard Chart of Accounts already ships 'Tax Assets' (Asset) and
		'Duties and Taxes' (Liability) groups - the installer must land accounts
		there instead of creating a parallel 'VAT & WHT' group. Spec section 4."""
		accounts = create_core_tax_accounts(TEST_COMPANY)

		input_vat = frappe.get_doc("Account", accounts["input_vat_account"])
		output_vat = frappe.get_doc("Account", accounts["output_vat_account"])

		self.assertEqual(input_vat.parent_account, frappe.db.get_value(
			"Account", {"account_name": "Tax Assets", "company": TEST_COMPANY}, "name"
		))
		self.assertEqual(output_vat.parent_account, frappe.db.get_value(
			"Account", {"account_name": "Duties and Taxes", "company": TEST_COMPANY}, "name"
		))
		self.assertFalse(frappe.db.exists("Account", {"account_name": "VAT & WHT", "company": TEST_COMPANY}))

	def test_is_idempotent(self):
		first_run = create_core_tax_accounts(TEST_COMPANY)
		second_run = create_core_tax_accounts(TEST_COMPANY)

		self.assertEqual(first_run, second_run)
		for account_name in first_run.values():
			count = frappe.db.count(
				"Account", {"account_name": account_name.split(" - ")[0], "company": TEST_COMPANY}
			)
			self.assertEqual(count, 1)


class TestCreateTaxCategories(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_creates_the_four_standard_categories(self):
		names = create_tax_categories()
		self.assertEqual(names, TAX_CATEGORIES)
		for title in TAX_CATEGORIES:
			self.assertTrue(frappe.db.exists("Tax Category", title))

	def test_is_idempotent(self):
		create_tax_categories()
		names = create_tax_categories()
		self.assertEqual(len(names), len(TAX_CATEGORIES))


class TestCreateVatTemplates(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_templates_wire_correct_account_heads_and_rate(self):
		accounts = create_core_tax_accounts(TEST_COMPANY)
		templates = create_vat_templates(TEST_COMPANY, accounts)

		sales = frappe.get_doc("Sales Taxes and Charges Template", templates["sales_vat_template"])
		self.assertEqual(sales.taxes[0].account_head, accounts["output_vat_account"])
		self.assertEqual(sales.taxes[0].rate, 16)
		self.assertFalse(sales.taxes[0].included_in_print_rate)

		purchase = frappe.get_doc("Purchase Taxes and Charges Template", templates["purchase_vat_template"])
		self.assertEqual(purchase.taxes[0].account_head, accounts["input_vat_account"])
		self.assertEqual(purchase.taxes[0].rate, 16)
		self.assertFalse(purchase.taxes[0].included_in_print_rate)
		self.assertEqual(purchase.taxes[0].category, "Total")

		inclusive = frappe.get_doc(
			"Purchase Taxes and Charges Template", templates["purchase_vat_inclusive_template"]
		)
		self.assertEqual(inclusive.taxes[0].account_head, accounts["input_vat_account"])
		self.assertTrue(inclusive.taxes[0].included_in_print_rate)

	def test_is_idempotent(self):
		accounts = create_core_tax_accounts(TEST_COMPANY)
		first_run = create_vat_templates(TEST_COMPANY, accounts)
		second_run = create_vat_templates(TEST_COMPANY, accounts)
		self.assertEqual(first_run, second_run)


class TestCreateWhtCategories(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_creates_categories_with_rate_and_company_account_row(self):
		accounts = create_core_tax_accounts(TEST_COMPANY)
		names = create_wht_categories(TEST_COMPANY, accounts["wht_payable_account"])

		self.assertEqual(set(names), {name for name, _rate in WHT_CATEGORIES})
		for category_name, rate in WHT_CATEGORIES:
			doc = frappe.get_doc("Tax Withholding Category", category_name)
			self.assertEqual(doc.rates[0].tax_withholding_rate, rate)
			company_rows = [row for row in doc.accounts if row.company == TEST_COMPANY]
			self.assertEqual(len(company_rows), 1)
			self.assertEqual(company_rows[0].account, accounts["wht_payable_account"])

	def test_second_company_adds_a_row_instead_of_a_duplicate_category(self):
		"""Spec section 11: a company can withhold from suppliers *and* have tax
		withheld by its own customers - the category is shared, the account row
		is per-company."""
		accounts = create_core_tax_accounts(TEST_COMPANY)
		create_wht_categories(TEST_COMPANY, accounts["wht_payable_account"])

		other_company = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": "KA Second Test Co",
				"abbr": "KASC",
				"default_currency": "KES",
				"country": "Kenya",
			}
		).insert(ignore_permissions=True)
		other_accounts = create_core_tax_accounts(other_company.name)
		create_wht_categories(other_company.name, other_accounts["wht_payable_account"])

		category = frappe.get_doc("Tax Withholding Category", WHT_CATEGORIES[0][0])
		companies = {row.company for row in category.accounts}
		self.assertEqual(companies, {TEST_COMPANY, other_company.name})
