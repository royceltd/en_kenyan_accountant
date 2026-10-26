# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Tests for the setup/ installer functions directly (not through the Kenyan
Accountant Settings doctype) - accounts.py, vat.py and wht.py each own one
concern from the spec and are tested against that concern in isolation.
See kenya_accounting_settings/test_kenya_accounting_settings.py for the
orchestration/lifecycle tests that go through the doctype.
"""

import frappe
from frappe.tests import IntegrationTestCase

from kenyan_accountant.setup.accounts import CORE_TAX_ACCOUNTS, create_core_tax_accounts
from kenyan_accountant.setup.pricing import apply_pricing_profile
from kenyan_accountant.setup.utils import TEST_COMPANY
from kenyan_accountant.setup.vat import (
	ITEM_TAX_TEMPLATES,
	ROLE_ACCOUNT_FIELDS,
	TAX_CATEGORIES,
	add_non_recoverable_row,
	create_item_tax_templates,
	create_tax_categories,
	create_vat_templates,
	disable_erpnext_default_kenya_tax_templates,
)
from kenyan_accountant.setup.vehicles import (
	VEHICLE_EXPENSE_ITEMS,
	create_vehicle_accounts,
	create_vehicle_expense_items,
)
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

	def test_purchase_templates_carry_a_0_percent_non_recoverable_row(self):
		accounts = create_core_tax_accounts(TEST_COMPANY)
		templates = create_vat_templates(TEST_COMPANY, accounts)
		for field in (
			"purchase_vat_template",
			"purchase_vat_inclusive_template",
			"purchase_reduced_vat_inclusive_template",
		):
			doc = frappe.get_doc("Purchase Taxes and Charges Template", templates[field])
			rows = {row.account_head: row for row in doc.taxes}
			self.assertEqual(rows[accounts["non_recoverable_vat_account"]].rate, 0, field)
			# Same inclusive/exclusive treatment as the VAT row it sits beside.
			self.assertEqual(
				rows[accounts["non_recoverable_vat_account"]].included_in_print_rate,
				rows[accounts["input_vat_account"]].included_in_print_rate,
			)

	def test_inclusive_and_reduced_sales_templates(self):
		accounts = create_core_tax_accounts(TEST_COMPANY)
		templates = create_vat_templates(TEST_COMPANY, accounts)
		inclusive = frappe.get_doc("Sales Taxes and Charges Template", templates["sales_vat_inclusive_template"])
		self.assertEqual((inclusive.taxes[0].rate, inclusive.taxes[0].included_in_print_rate), (16, 1))
		reduced = frappe.get_doc(
			"Sales Taxes and Charges Template", templates["sales_reduced_vat_inclusive_template"]
		)
		self.assertEqual((reduced.taxes[0].rate, reduced.taxes[0].included_in_print_rate), (8, 1))

	def test_non_recoverable_row_only_added_to_untouched_templates(self):
		"""ADR-023: a template the accountant changed is theirs."""
		accounts = create_core_tax_accounts(TEST_COMPANY)
		without = {k: v for k, v in accounts.items() if k != "non_recoverable_vat_account"}
		templates = create_vat_templates(TEST_COMPANY, without)  # as shipped before v16.6
		customised = frappe.get_doc("Purchase Taxes and Charges Template", templates["purchase_vat_template"])
		customised.taxes[0].description = "Edited by the accountant"
		customised.append(
			"taxes",
			{
				"charge_type": "Actual",
				"account_head": accounts["wht_payable_account"],
				"description": "Their own extra row",
				"tax_amount": 0,
				"category": "Total",
				"add_deduct_tax": "Add",
			},
		)
		customised.save()

		updated, skipped = add_non_recoverable_row(TEST_COMPANY, accounts)
		self.assertIn(templates["purchase_vat_inclusive_template"], updated)
		self.assertIn(customised.name, skipped)
		self.assertEqual(len(frappe.get_doc(customised.doctype, customised.name).taxes), 2)
		self.assertEqual(add_non_recoverable_row(TEST_COMPANY, accounts), ([], [customised.name]))

	def test_is_idempotent(self):
		accounts = create_core_tax_accounts(TEST_COMPANY)
		first_run = create_vat_templates(TEST_COMPANY, accounts)
		second_run = create_vat_templates(TEST_COMPANY, accounts)
		self.assertEqual(first_run, second_run)


class TestCreateItemTaxTemplates(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_each_template_carries_its_rates(self):
		accounts = create_core_tax_accounts(TEST_COMPANY)
		templates = create_item_tax_templates(TEST_COMPANY, accounts)
		self.assertEqual(sorted(templates), sorted(ITEM_TAX_TEMPLATES))
		for title, name in templates.items():
			doc = frappe.get_doc("Item Tax Template", name)
			self.assertEqual(doc.company, TEST_COMPANY)
			rates = {row.tax_type: row.tax_rate for row in doc.taxes}
			expected = {
				accounts[ROLE_ACCOUNT_FIELDS[role]]: rate for role, rate in ITEM_TAX_TEMPLATES[title].items()
			}
			self.assertEqual(rates, expected, title)

	def test_zero_rated_and_exempt_are_0_percent_on_both_vat_accounts(self):
		accounts = create_core_tax_accounts(TEST_COMPANY)
		templates = create_item_tax_templates(TEST_COMPANY, accounts)
		for title in ("KE Zero Rated", "KE Exempt"):
			rates = {row.tax_type: row.tax_rate for row in frappe.get_doc("Item Tax Template", templates[title]).taxes}
			self.assertEqual(rates, {accounts["output_vat_account"]: 0, accounts["input_vat_account"]: 0})

	def test_non_claimable_moves_purchase_vat_to_the_expense_account(self):
		accounts = create_core_tax_accounts(TEST_COMPANY)
		templates = create_item_tax_templates(TEST_COMPANY, accounts)
		rates = {
			row.tax_type: row.tax_rate
			for row in frappe.get_doc("Item Tax Template", templates["KE Non-Claimable 8%"]).taxes
		}
		self.assertEqual(rates[accounts["input_vat_account"]], 0)
		self.assertEqual(rates[accounts["non_recoverable_vat_account"]], 8)

	def test_is_idempotent_and_keeps_a_client_edited_template(self):
		accounts = create_core_tax_accounts(TEST_COMPANY)
		first = create_item_tax_templates(TEST_COMPANY, accounts)
		frappe.db.set_value("Item Tax Template", first["KE Exempt"], "disabled", 1)
		second = create_item_tax_templates(TEST_COMPANY, accounts)
		self.assertEqual(first, second)
		self.assertEqual(frappe.db.get_value("Item Tax Template", first["KE Exempt"], "disabled"), 1)


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
		# Superset, not equality: this category is shared/global and may already
		# carry rows for real companies from real (non-test) usage of the app -
		# what matters is that *our two* companies both got a row, not that
		# they're the only rows on the document.
		self.assertTrue({TEST_COMPANY, other_company.name} <= companies)
		# And still exactly one row per company - no duplicate for either.
		self.assertEqual(
			len([row for row in category.accounts if row.company in (TEST_COMPANY, other_company.name)]), 2
		)


class TestDisableErpnextDefaultKenyaTaxTemplates(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_disables_erpnexts_own_kenya_tax_templates(self):
		"""TEST_COMPANY has country=Kenya, so Company's own controller already
		auto-created ERPNext's generic 'Kenya Tax' Sales/Purchase templates
		(erpnext.setup.doctype.company.company -> setup_taxes_and_charges) -
		exactly the real-world scenario this function exists to clean up."""
		for doctype in ("Sales Taxes and Charges Template", "Purchase Taxes and Charges Template"):
			name = frappe.db.get_value(doctype, {"title": "Kenya Tax", "company": TEST_COMPANY}, "name")
			self.assertTrue(name, f"expected setUp to have an ERPNext-default Kenya Tax {doctype}")
			self.assertFalse(frappe.db.get_value(doctype, name, "disabled"))

		disabled = disable_erpnext_default_kenya_tax_templates(TEST_COMPANY)
		self.assertEqual(len(disabled), 2)

		for doctype in ("Sales Taxes and Charges Template", "Purchase Taxes and Charges Template"):
			name = frappe.db.get_value(doctype, {"title": "Kenya Tax", "company": TEST_COMPANY}, "name")
			self.assertTrue(frappe.db.get_value(doctype, name, "disabled"))

	def test_does_not_touch_our_own_templates(self):
		accounts = create_core_tax_accounts(TEST_COMPANY)
		templates = create_vat_templates(TEST_COMPANY, accounts)
		disable_erpnext_default_kenya_tax_templates(TEST_COMPANY)

		sales = frappe.get_doc("Sales Taxes and Charges Template", templates["sales_vat_template"])
		self.assertFalse(sales.disabled)

	def test_is_idempotent(self):
		disable_erpnext_default_kenya_tax_templates(TEST_COMPANY)
		second_run = disable_erpnext_default_kenya_tax_templates(TEST_COMPANY)
		self.assertEqual(second_run, [])


class TestVehicles(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_accounts_and_items_map_vat_by_vehicle_class(self):
		accounts = create_core_tax_accounts(TEST_COMPANY)
		item_tax_templates = create_item_tax_templates(TEST_COMPANY, accounts)
		vehicle_accounts = create_vehicle_accounts(TEST_COMPANY)
		self.assertEqual(frappe.db.get_value("Account", vehicle_accounts["motor_vehicles"], "account_type"), "Fixed Asset")

		create_vehicle_expense_items(TEST_COMPANY, vehicle_accounts, item_tax_templates)
		for item_code, account_key, tax_title, _uom in VEHICLE_EXPENSE_ITEMS:
			item = frappe.get_doc("Item", item_code)
			self.assertFalse(item.is_stock_item)
			defaults = {row.company: row.expense_account for row in item.item_defaults}
			self.assertEqual(defaults[TEST_COMPANY], vehicle_accounts[account_key])
			self.assertIn(item_tax_templates[tax_title], [row.item_tax_template for row in item.taxes])

		# Items are shared across companies; each company has its own row.
		def template_for(item_code):
			for row in frappe.get_doc("Item", item_code).taxes:
				if frappe.db.get_value("Item Tax Template", row.item_tax_template, "company") == TEST_COMPANY:
					return row.item_tax_template

		self.assertEqual(template_for("Fuel - Passenger Vehicle"), item_tax_templates["KE Non-Claimable 8%"])
		self.assertEqual(template_for("Fuel - Commercial Vehicle"), item_tax_templates["KE Reduced 8%"])

	def test_is_idempotent(self):
		accounts = create_core_tax_accounts(TEST_COMPANY)
		item_tax_templates = create_item_tax_templates(TEST_COMPANY, accounts)
		vehicle_accounts = create_vehicle_accounts(TEST_COMPANY)
		create_vehicle_expense_items(TEST_COMPANY, vehicle_accounts, item_tax_templates)
		self.assertEqual(create_vehicle_accounts(TEST_COMPANY), vehicle_accounts)
		self.assertEqual(create_vehicle_expense_items(TEST_COMPANY, vehicle_accounts, item_tax_templates), [])


class TestPricingProfile(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_profile_ticks_company_defaults(self):
		accounts = create_core_tax_accounts(TEST_COMPANY)
		settings = frappe._dict(company=TEST_COMPANY, **create_vat_templates(TEST_COMPANY, accounts))
		for profile, sales_field, purchase_field in (
			("Retail / POS", "sales_vat_inclusive_template", "purchase_vat_inclusive_template"),
			("Fuel Station", "sales_reduced_vat_inclusive_template", "purchase_reduced_vat_inclusive_template"),
			("B2B / Services", "sales_vat_template", "purchase_vat_template"),
		):
			settings.pricing_profile = profile
			apply_pricing_profile(settings)
			for doctype, field in (
				("Sales Taxes and Charges Template", sales_field),
				("Purchase Taxes and Charges Template", purchase_field),
			):
				defaults = frappe.get_all(doctype, filters={"company": TEST_COMPANY, "is_default": 1}, pluck="name")
				self.assertEqual(defaults, [settings[field]], profile)
