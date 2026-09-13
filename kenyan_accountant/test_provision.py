# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Tests for setup/provision.py -- the bootstrap entry point a fully automated
provisioning pipeline calls for a site that has no Company yet. See
kenyan_accountant/test_setup.py for the already-has-a-Company tests these build on."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from kenyan_accountant.setup.provision import _generate_abbr, provision, provision_company

NEW_COMPANY = "Royce Provision Test Co"


class TestGenerateAbbr(IntegrationTestCase):
	def test_multi_word_name_uses_initials(self):
		self.assertEqual(_generate_abbr("Acme Trading Ltd"), "ATL")

	def test_single_word_name_uses_leading_characters(self):
		self.assertEqual(_generate_abbr("Acme"), "ACME")

	def test_ignores_punctuation(self):
		self.assertEqual(_generate_abbr("Acme & Sons (Kenya)"), "ASK")


class TestProvisionCompany(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_creates_a_new_company_with_kenya_defaults(self):
		self.assertFalse(frappe.db.exists("Company", NEW_COMPANY))

		result = provision_company(NEW_COMPANY)

		self.assertEqual(result, NEW_COMPANY)
		company = frappe.get_doc("Company", NEW_COMPANY)
		self.assertEqual(company.country, "Kenya")
		self.assertEqual(company.default_currency, "KES")

	def test_is_idempotent(self):
		first = provision_company(NEW_COMPANY)
		second = provision_company(NEW_COMPANY)
		self.assertEqual(first, second)
		self.assertEqual(frappe.db.count("Company", {"company_name": NEW_COMPANY}), 1)

	def test_marks_frappe_and_erpnext_setup_complete_on_a_fresh_site(self):
		"""Guards a real bug an actual customer hit in production: frappe.
		is_setup_complete() -- what the desk uses to decide whether to redirect a
		fresh login to /desk/setup-wizard/0 instead of the desk itself -- checks
		Installed Application.is_setup_complete per app, not whether a Company
		exists. Creating one by hand (as provision_company itself does) never
		touched that flag, so every real signup was landing a brand-new customer
		on the setup wizard instead of their own desk.

		Forces is_setup_complete() to report False -- this shared test bench
		already has it True from its own history, the same reason
		test_seeds_the_setup_wizards_own_fixtures_first can't reproduce the
		Warehouse Type bug it guards either -- so this actually exercises the
		branch instead of silently skipping it."""
		with patch("frappe.is_setup_complete", return_value=False), \
			patch("frappe.desk.page.setup_wizard.setup_wizard.enable_setup_wizard_complete") as mock_enable:
			provision_company(NEW_COMPANY)

		mock_enable.assert_any_call("frappe")
		mock_enable.assert_any_call("erpnext")

	def test_does_not_touch_the_wizard_flag_on_an_already_set_up_site(self):
		"""The flip side of the test above: a site that already has a Company
		(this shared test bench's real state, same as any already-configured real
		tenant) must not have this function re-poke a flag that's either already
		correct or, worse, someone's own site config the wizard already set up."""
		provision_company(NEW_COMPANY)  # first call: real Company created
		with patch("frappe.desk.page.setup_wizard.setup_wizard.enable_setup_wizard_complete") as mock_enable:
			provision_company(NEW_COMPANY)  # second call: already exists, early return
		mock_enable.assert_not_called()

	def test_seeds_the_setup_wizards_own_fixtures_first(self):
		"""Guards the real bug this function exists to fix: Company's own
		on_update hook (create_default_warehouses) unconditionally needs a
		"Transit" Warehouse Type to exist, normally seeded by the setup wizard --
		which fully automated provisioning never runs. Found against a genuinely
		fresh real site, not caught here: this shared test bench already has it
		from Frappe's own test-site bootstrap, so this assertion mainly documents
		the expectation and would catch a regression that removed the call
		entirely, not the original bug itself -- see provision_company's own
		comment for the real story."""
		provision_company(NEW_COMPANY)
		self.assertTrue(frappe.db.exists("Warehouse Type", "Transit"))


class TestProvision(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_creates_company_and_runs_kenyan_accountant_setup(self):
		self.assertFalse(frappe.db.exists("Company", NEW_COMPANY))

		result = provision(NEW_COMPANY)

		self.assertEqual(result["company"], NEW_COMPANY)
		self.assertTrue(frappe.db.exists("Company", NEW_COMPANY))
		settings = frappe.get_doc("Kenyan Accountant Settings", NEW_COMPANY)
		self.assertTrue(settings.input_vat_account)
		self.assertTrue(settings.sales_vat_template)
		self.assertIn(settings.setup_status, ("Draft Configuration",))

	def test_is_idempotent(self):
		first = provision(NEW_COMPANY)
		second = provision(NEW_COMPANY)
		self.assertEqual(first["kenyan_accountant_settings"], second["kenyan_accountant_settings"])
		self.assertEqual(
			frappe.db.count("Kenyan Accountant Settings", {"company": NEW_COMPANY}), 1
		)
