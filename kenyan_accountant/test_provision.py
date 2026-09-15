# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Tests for setup/provision.py -- the bootstrap entry point a fully automated
provisioning pipeline calls for a site that has no Company yet. See
kenyan_accountant/test_setup.py for the already-has-a-Company tests these build on."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from frappe.utils import getdate

from kenyan_accountant.setup.provision import (
	_ensure_fiscal_year,
	_ensure_genders,
	_ensure_global_defaults,
	_ensure_price_lists,
	_generate_abbr,
	provision,
	provision_company,
)

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

	def test_clears_cache_after_marking_setup_complete_on_a_fresh_site(self):
		"""Guards a second real bug, found after the one above shipped: a fresh
		tenant landed correctly on /desk (not the wizard) but reloaded itself
		endlessly. enable_setup_wizard_complete() writes via frappe.db.set_value(),
		a raw SQL write that never invalidates frappe.client_cache -- a separate
		Redis-backed cache frappe.boot.get_bootinfo() reads via
		get_setup_wizard_completed_apps(), distinct from the fresh DB query
		frappe.is_setup_complete() itself uses. Without a clear, every /desk load
		kept shipping the client stale bootinfo still claiming frappe/erpnext
		hadn't finished their wizard, which is what sent the desk's own JS back
		into wizard-init logic in a loop -- confirmed live: `bench clear-cache`
		alone broke an actual stuck tenant's loop instantly. frappe's own
		wizard-completion pipeline always calls this right after setting the same
		flag, for this exact reason.

		Asserts a no-arg call specifically, not just "called" -- install_fixtures
		itself (exercised here too, since first_company is forced True) makes its
		own incidental frappe.clear_cache(doctype=...) calls as an ordinary side
		effect of inserting master data, same as any other document insert. Those
		are routine and not what this test guards; the global clear_cache() this
		function adds is."""
		with patch("frappe.is_setup_complete", return_value=False), \
			patch("frappe.desk.page.setup_wizard.setup_wizard.enable_setup_wizard_complete"), \
			patch("frappe.clear_cache") as mock_clear_cache:
			provision_company(NEW_COMPANY)

		mock_clear_cache.assert_any_call()

	def test_does_not_clear_cache_on_an_already_set_up_site(self):
		"""Flip side: an already-configured site's cache shouldn't be blown away
		on every subsequent provision_company() call -- clearing it is only ever
		needed right after the flag itself changes."""
		provision_company(NEW_COMPANY)  # first call: real Company created
		with patch("frappe.clear_cache") as mock_clear_cache:
			provision_company(NEW_COMPANY)  # second call: already exists, early return
		mock_clear_cache.assert_not_called()

	def test_resets_home_page_away_from_setup_wizard_on_a_fresh_site(self):
		"""Guards a third real bug, found after the cache fix above turned out
		not to be sufficient by itself: a fresh tenant's desk reload-looped
		itself, and the actual, complete explanation was a separate site
		default -- frappe.utils.install seeds every new site with
		desktop:home_page = "setup-wizard" unconditionally at `bench new-site`
		time, and nothing but the wizard's own completion page (which this
		fully-automated pipeline deliberately never runs) ever resets it. Left
		alone, every /desk boot keeps reporting home_page: "setup-wizard"
		forever; that page's own on_page_load handler sees setup_complete is
		already true, tries to leave via window.location.href = "/desk" -- and
		the next load reports the identical wrong home_page again. Confirmed
		live against an actual stuck tenant, isolated from the cache fix above:
		its real (authenticated, not guessed) boot info showed home_page:
		"setup-wizard" even with setup_complete already true; setting this one
		default to "workspace" and re-fetching flipped it to a real page and the
		reload cycle -- visible until then in nginx's access log every ~1-2s --
		did not resume.

		Asserts a specific call, not just "called once" -- install_fixtures
		itself (exercised here too, since first_company is forced True) makes
		its own incidental frappe.db.set_default(...) calls, seeding unrelated
		Selling/Buying Settings defaults as an ordinary side effect. Those are
		routine and not what this test guards; the "desktop:home_page" one this
		function adds is."""
		with patch("frappe.is_setup_complete", return_value=False), \
			patch("frappe.desk.page.setup_wizard.setup_wizard.enable_setup_wizard_complete"), \
			patch("frappe.db.set_default") as mock_set_default:
			provision_company(NEW_COMPANY)

		mock_set_default.assert_any_call("desktop:home_page", "workspace")

	def test_does_not_reset_home_page_on_an_already_set_up_site(self):
		"""Flip side: an already-configured site's home page shouldn't be reset
		on every subsequent provision_company() call -- a real customer may
		since have picked their own."""
		provision_company(NEW_COMPANY)  # first call: real Company created
		with patch("frappe.db.set_default") as mock_set_default:
			provision_company(NEW_COMPANY)  # second call: already exists, early return
		mock_set_default.assert_not_called()

	def test_seeds_this_and_next_years_fiscal_year(self):
		"""Guards a real gap: without a Fiscal Year covering today's date, the
		first invoice/journal entry/payroll run a real customer attempts fails
		outright ("no fiscal year found") -- same "wizard normally seeds this,
		this pipeline never runs the wizard" category as the Warehouse Type bug
		above. Checks coverage by date range, not an exact "{year}"-named
		record -- found for real against this shared test bench, which already
		carries real leftover Fiscal Years from its own history (created by
		earlier test runs against this same persistent site, not by anything
		a genuinely fresh production tenant would ever have -- Fiscal Year
		seeding here only ever runs once, at Company creation, before
		anything else on the site could have seeded a competing one), and
		one of them ("2026-2027", a Jul-Jun year) only partially overlaps
		next year -- asserting full date coverage for next year would assert
		something that happens to be false on THIS bench specifically while
		being structurally unreachable in real production. Asserts the
		calls instead, which is true regardless of what the bench's own
		history happens to contain."""
		current_year = getdate().year
		with patch("kenyan_accountant.setup.provision._ensure_fiscal_year") as mock_ensure:
			provision_company(NEW_COMPANY)
		mock_ensure.assert_any_call(current_year)
		mock_ensure.assert_any_call(current_year + 1)

	def test_does_not_duplicate_coverage_for_an_already_covered_year(self):
		"""A year that's already covered (whether by this function's own
		earlier run, a customer's custom Fiscal Year, or -- on this shared
		test bench specifically -- another test's own fixture) must not get a
		second, overlapping one -- ERPNext's own overlap validation would
		throw, not silently no-op, if this tried anyway (found running this
		for real, not assumed)."""
		current_year = getdate().year
		count_before = frappe.db.count(
			"Fiscal Year",
			{"year_start_date": ["<=", f"{current_year}-01-01"], "year_end_date": [">=", f"{current_year}-12-31"]},
		)
		provision_company(NEW_COMPANY)
		provision_company(f"{NEW_COMPANY} 2")
		count_after = frappe.db.count(
			"Fiscal Year",
			{"year_start_date": ["<=", f"{current_year}-01-01"], "year_end_date": [">=", f"{current_year}-12-31"]},
		)
		self.assertEqual(count_before, count_after)

	def test_reuses_an_overlapping_fiscal_year_under_a_different_name(self):
		"""Guards the real bug this exact test caught running against the
		shared test bench: that bench carries real leftover Fiscal Years from
		its own history (a "2026-2027" Jul-Jun year among them) that partially
		overlap a plain calendar year without being named after it at all --
		ERPNext's own Fiscal Year.validate_overlap() is date-range based, and
		threw a NameError the first time this ran for real, instead of the
		naive exact-name check anticipating it.

		Reproduces that shape directly against _ensure_fiscal_year (not
		provision_company, and not "this year"/"next year") using a sentinel
		far-future year -- so this test's own setup can't itself collide with
		whatever the shared bench's real, ordinary fixtures happen to be
		using around today's actual date."""
		year = 2090
		frappe.get_doc(
			{
				"doctype": "Fiscal Year",
				"year": f"Some Other Name {year}",
				"year_start_date": f"{year}-06-01",
				"year_end_date": f"{year + 1}-05-31",
			}
		).insert(ignore_permissions=True)

		result = _ensure_fiscal_year(year)  # must not throw

		self.assertEqual(result, f"Some Other Name {year}")
		self.assertFalse(frappe.db.exists("Fiscal Year", str(year)))

	def test_seeds_standard_price_lists_on_a_fresh_site(self):
		"""Guards the "New Item Price has nothing to pick from" bug: a fresh site
		had neither of ERPNext's own factory-default Price Lists, since those are
		normally created by the setup wizard's install_defaults(), not
		install_fixtures() (the only wizard piece provision_company already ran
		before this fix). Forces first_company True the same way the other
		fresh-site tests above do -- this shared bench likely already carries
		both Price Lists from its own history, so without that this would mainly
		document intent rather than exercise the branch."""
		with patch("frappe.is_setup_complete", return_value=False), \
			patch("frappe.desk.page.setup_wizard.setup_wizard.enable_setup_wizard_complete"):
			provision_company(NEW_COMPANY)

		for name in ("Standard Buying", "Standard Selling"):
			self.assertTrue(frappe.db.exists("Price List", name))
			price_list = frappe.get_doc("Price List", name)
			self.assertEqual(price_list.currency, "KES")

	def test_does_not_duplicate_price_lists_on_a_second_fresh_company(self):
		"""_ensure_price_lists names its records after ERPNext's own fixed
		"Standard Buying"/"Standard Selling" pair (not per-company) -- a second
		tenant's provisioning must reuse them, not throw a DuplicateEntryError."""
		provision_company(NEW_COMPANY)
		provision_company(f"{NEW_COMPANY} 2")  # must not throw
		self.assertEqual(frappe.db.count("Price List", {"price_list_name": "Standard Buying"}), 1)
		self.assertEqual(frappe.db.count("Price List", {"price_list_name": "Standard Selling"}), 1)

	def test_sets_global_defaults_on_a_fresh_site(self):
		"""Guards the "Company is required" / Price List defaulting to INR bugs:
		both trace back to Global Defaults.default_company/default_currency never
		being set, since this pipeline never runs the setup wizard's own
		set_global_defaults()."""
		with patch("frappe.is_setup_complete", return_value=False), \
			patch("frappe.desk.page.setup_wizard.setup_wizard.enable_setup_wizard_complete"):
			provision_company(NEW_COMPANY)

		global_defaults = frappe.get_single("Global Defaults")
		self.assertEqual(global_defaults.default_company, NEW_COMPANY)
		self.assertEqual(global_defaults.default_currency, "KES")
		self.assertTrue(frappe.db.get_value("Currency", "KES", "enabled"))

	def test_does_not_touch_global_defaults_on_an_already_set_up_site(self):
		"""Flip side: an already-configured site's Global Defaults shouldn't be
		overwritten on every subsequent provision_company() call -- a real
		customer, or a second Royce app's own setup, may since have changed
		default_company deliberately (multi-company is out of scope for this
		pipeline, but nothing here should assume it can never happen)."""
		provision_company(NEW_COMPANY)
		with patch("kenyan_accountant.setup.provision._ensure_global_defaults") as mock_ensure:
			provision_company(NEW_COMPANY)  # second call: already exists, early return
		mock_ensure.assert_not_called()

	def test_seeds_standard_genders_on_a_fresh_site(self):
		"""Guards the "no Gender to pick from" bug: Employee.gender is `reqd: 1`
		with nothing to select, since Gender is a frappe-core fixture this
		pipeline (which only ever calls erpnext's own install_fixtures) never
		seeds."""
		with patch("frappe.is_setup_complete", return_value=False), \
			patch("frappe.desk.page.setup_wizard.setup_wizard.enable_setup_wizard_complete"):
			provision_company(NEW_COMPANY)

		for gender in ("Male", "Female", "Other"):
			self.assertTrue(frappe.db.exists("Gender", gender))

	def test_does_not_duplicate_genders_on_a_second_fresh_company(self):
		provision_company(NEW_COMPANY)
		provision_company(f"{NEW_COMPANY} 2")  # must not throw
		self.assertEqual(frappe.db.count("Gender", {"gender": "Male"}), 1)

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


class TestEnsureHelpers(IntegrationTestCase):
	"""Direct, isolated tests for the three private helpers provision_company()
	calls on a fresh site -- narrower than going through provision_company()
	itself (see the fresh-site tests above), so a failure here points straight
	at the helper responsible instead of the whole first_company branch."""

	def tearDown(self):
		frappe.db.rollback()

	def test_ensure_price_lists_is_idempotent(self):
		_ensure_price_lists("KES")
		_ensure_price_lists("KES")  # must not throw DuplicateEntryError
		self.assertEqual(frappe.db.count("Price List", {"price_list_name": "Standard Buying"}), 1)

	def test_ensure_global_defaults_sets_company_and_currency(self):
		_ensure_global_defaults(NEW_COMPANY, "Kenya", "KES")
		global_defaults = frappe.get_single("Global Defaults")
		self.assertEqual(global_defaults.default_company, NEW_COMPANY)
		self.assertEqual(global_defaults.default_currency, "KES")
		self.assertEqual(global_defaults.country, "Kenya")

	def test_ensure_genders_is_idempotent(self):
		_ensure_genders()
		_ensure_genders()  # must not throw DuplicateEntryError
		self.assertEqual(frappe.db.count("Gender", {"gender": "Male"}), 1)


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
