# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Tests for setup/site_defaults.py -- the setup-wizard steps (System Settings,
frappe fixtures, stock defaults) and Kenya payment defaults (M-Pesa, Bank) that a
2026-09-24 audit found missing on every live tenant."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from kenyan_accountant.setup.provision import provision_company
from kenyan_accountant.setup.site_defaults import (
	_disable_currency_if_unused,
	_fix_user_timezones,
	backfill_site_defaults,
	seed_payment_defaults,
)

NEW_COMPANY = "Royce Site Defaults Test Co"


def _fresh_company():
	"""A real Company created through the fresh-site branch, the same way the other
	fresh-site tests in test_provision.py force it on this long-lived bench."""
	with patch("frappe.is_setup_complete", return_value=False), \
		patch("frappe.desk.page.setup_wizard.setup_wizard.enable_setup_wizard_complete"):
		return provision_company(NEW_COMPANY)


class TestSeedPaymentDefaults(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_creates_mpesa_and_bank_ledgers_under_bank_accounts(self):
		company = _fresh_company()
		for account_name in ("M-Pesa", "Bank"):
			account = frappe.db.get_value(
				"Account",
				{"account_name": account_name, "company": company},
				["account_type", "root_type", "is_group", "account_currency"],
				as_dict=True,
			)
			self.assertIsNotNone(account, account_name)
			self.assertEqual(account.account_type, "Bank")
			self.assertEqual(account.root_type, "Asset")
			self.assertEqual(account.is_group, 0)
			self.assertEqual(account.account_currency, "KES")

	def test_sets_bank_ledger_as_company_default_bank_account(self):
		company = _fresh_company()
		bank = frappe.db.get_value("Account", {"account_name": "Bank", "company": company})
		self.assertEqual(frappe.db.get_value("Company", company, "default_bank_account"), bank)

	def test_does_not_overwrite_an_existing_default_bank_account(self):
		company = _fresh_company()
		mpesa = frappe.db.get_value("Account", {"account_name": "M-Pesa", "company": company})
		frappe.db.set_value("Company", company, "default_bank_account", mpesa)
		frappe.clear_document_cache("Company", company)

		seed_payment_defaults(company)

		self.assertEqual(frappe.db.get_value("Company", company, "default_bank_account"), mpesa)

	def test_mpesa_mode_of_payment_points_at_mpesa_ledger(self):
		company = _fresh_company()
		mode = frappe.get_doc("Mode of Payment", "M-Pesa")
		self.assertEqual(mode.type, "Phone")
		row = next(r for r in mode.accounts if r.company == company)
		self.assertEqual(
			row.default_account, frappe.db.get_value("Account", {"account_name": "M-Pesa", "company": company})
		)

	def test_bank_settled_modes_default_to_bank_ledger(self):
		company = _fresh_company()
		bank = frappe.db.get_value("Account", {"account_name": "Bank", "company": company})
		for mode in ("Wire Transfer", "Bank Draft", "Cheque", "Credit Card"):
			if not frappe.db.exists("Mode of Payment", mode):
				continue
			self.assertEqual(
				frappe.db.get_value("Mode of Payment Account", {"parent": mode, "company": company}, "default_account"),
				bank,
				mode,
			)

	def test_is_idempotent(self):
		company = _fresh_company()
		self.assertEqual(seed_payment_defaults(company), [])  # second run: nothing left to do
		self.assertEqual(frappe.db.count("Account", {"account_name": "M-Pesa", "company": company}), 1)
		self.assertEqual(frappe.db.count("Mode of Payment Account", {"parent": "M-Pesa", "company": company}), 1)


class TestApplyWizardDefaults(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()
		frappe.clear_cache()

	def test_sets_kenya_system_settings(self):
		_fresh_company()
		settings = frappe.get_single("System Settings")
		self.assertEqual(settings.time_zone, "Africa/Nairobi")
		self.assertEqual(settings.country, "Kenya")
		self.assertEqual(settings.currency, "KES")
		self.assertEqual(settings.date_format, "dd-mm-yyyy")

	def test_seeds_frappe_salutations(self):
		_fresh_company()
		for salutation in ("Mr", "Ms", "Mrs", "Dr", "Prof"):
			self.assertTrue(frappe.db.exists("Salutation", salutation), salutation)

	def test_sets_stock_defaults(self):
		company = _fresh_company()
		stock = frappe.get_single("Stock Settings")
		self.assertEqual(stock.stock_uom, "Nos")
		self.assertEqual(frappe.db.get_value("Warehouse", stock.default_warehouse, "warehouse_name"), "Stores")
		self.assertEqual(stock.email_footer_address, company)

	def test_moves_kolkata_users_to_nairobi(self):
		frappe.db.set_value("User", "Administrator", "time_zone", "Asia/Kolkata")
		_fresh_company()
		self.assertEqual(frappe.db.get_value("User", "Administrator", "time_zone"), "Africa/Nairobi")


class TestBackfillSiteDefaults(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()
		frappe.clear_cache()

	def _run(self, **kwargs):
		# backfill commits (it's a bench execute entry point); keep test data
		# rollback-able by stubbing the commit out.
		with patch("frappe.db.commit"):
			return backfill_site_defaults(**kwargs)

	def test_fills_blank_time_zone_and_frappe_default_date_format(self):
		frappe.db.set_single_value("System Settings", "time_zone", None)
		frappe.db.set_single_value("System Settings", "date_format", "yyyy-mm-dd")
		changes = self._run(enable_scheduler=False)
		self.assertEqual(frappe.db.get_single_value("System Settings", "time_zone"), "Africa/Nairobi")
		self.assertEqual(frappe.db.get_single_value("System Settings", "date_format"), "dd-mm-yyyy")
		self.assertIn("System Settings.time_zone", changes)

	def test_never_overwrites_a_chosen_value(self):
		frappe.db.set_single_value("System Settings", "time_zone", "Africa/Kampala")
		frappe.db.set_single_value("System Settings", "date_format", "mm-dd-yyyy")
		self._run(enable_scheduler=False)
		self.assertEqual(frappe.db.get_single_value("System Settings", "time_zone"), "Africa/Kampala")
		self.assertEqual(frappe.db.get_single_value("System Settings", "date_format"), "mm-dd-yyyy")

	def test_enables_scheduler_when_asked(self):
		frappe.db.set_single_value("System Settings", "enable_scheduler", 0)
		with patch("frappe.utils.scheduler.enable_scheduler") as mock_enable:
			self._run(enable_scheduler=True)
		mock_enable.assert_called_once()

	def test_leaves_scheduler_alone_for_demo(self):
		frappe.db.set_single_value("System Settings", "enable_scheduler", 0)
		with patch("frappe.utils.scheduler.enable_scheduler") as mock_enable:
			changes = self._run(enable_scheduler=False)
		mock_enable.assert_not_called()
		self.assertNotIn("System Settings.enable_scheduler", changes)


class TestBackfillGlobalDefaults(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_fills_blank_default_company_on_a_single_company_site(self):
		from kenyan_accountant.setup.site_defaults import _backfill_global_defaults

		with patch("frappe.get_all", return_value=["_Test Company"]):
			frappe.db.set_single_value("Global Defaults", "default_company", None)
			changes = _backfill_global_defaults()
		self.assertEqual(frappe.db.get_single_value("Global Defaults", "default_company"), "_Test Company")
		self.assertIn("Global Defaults.default_company", changes)

	def test_replaces_factory_inr_default_currency_on_a_kes_company(self):
		from kenyan_accountant.setup.site_defaults import _backfill_global_defaults

		company = frappe.db.get_value("Company", {"default_currency": "KES"}) or _fresh_company()
		frappe.db.set_single_value("Global Defaults", "default_currency", "INR")
		with patch("frappe.get_all", return_value=[company]):
			changes = _backfill_global_defaults()
		self.assertEqual(frappe.db.get_single_value("Global Defaults", "default_currency"), "KES")
		self.assertEqual(changes["Global Defaults.default_currency"], "KES")

	def test_leaves_multi_company_sites_alone(self):
		from kenyan_accountant.setup.site_defaults import _backfill_global_defaults

		with patch("frappe.get_all", return_value=["_Test Company", "_Test Company 1"]):
			self.assertEqual(_backfill_global_defaults(), {})


class TestHelpers(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_fix_user_timezones_leaves_a_real_choice_alone(self):
		frappe.db.set_value("User", "Administrator", "time_zone", "Europe/London")
		self.assertNotIn("Administrator", _fix_user_timezones())
		self.assertEqual(frappe.db.get_value("User", "Administrator", "time_zone"), "Europe/London")

	def test_does_not_disable_a_currency_in_use(self):
		frappe.db.set_value("Currency", "INR", "enabled", 1)
		with patch("frappe.db.exists", side_effect=lambda doctype, filters=None, *a, **k: doctype == "Price List"):
			self.assertFalse(_disable_currency_if_unused("INR"))
		self.assertTrue(frappe.db.get_value("Currency", "INR", "enabled"))
