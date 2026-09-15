# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Bootstrap entry point for a brand-new tenant with no Company yet.

Every other function in setup/ (accounts.py, vat.py, wht.py) and Kenyan Accountant
Settings' own run_setup() assume a Company already exists -- reasonable for an
existing ERPNext site where a human has already gone through the setup wizard, but
not true for a site created by fully automated provisioning, which skips that
wizard entirely (there is no human in the loop to answer it). provision() is the
one function that starts from nothing.

Deliberately kept in this app rather than a separate app or the calling platform's
own code: every current Royce plan includes kenyan_accountant unconditionally (it
is the one app every Kenyan tenant needs regardless of size), and Company creation
is a real prerequisite for this app's own setup either way -- see the parallel note
in royce_payroll_ke's install.py ("provisioning needs a real Company with its Chart
of Accounts already applied"). Housing it here means the platform-side caller
(Royce Control Plane) only needs one call, not a separate "create the Company"
step of its own.
"""

import re

import frappe
from frappe.utils import getdate


def _generate_abbr(company_name: str) -> str:
	"""A short, valid Company.abbr from a business name -- initials of each word
	(e.g. "Acme Trading Ltd" -> "ATL"), falling back to the first few alphanumeric
	characters for a single-word name (e.g. "Acme" -> "ACME"). Uniqueness is not
	this function's concern: a Royce-provisioned site has exactly one Company for
	its whole life, so there is nothing for a fresh abbr to collide with."""
	words = re.findall(r"[A-Za-z0-9]+", company_name)
	if len(words) > 1:
		abbr = "".join(word[0] for word in words[:5]).upper()
	else:
		abbr = re.sub(r"[^A-Za-z0-9]", "", company_name)[:5].upper()
	return abbr or "CO"


def _ensure_fiscal_year(year: int) -> str:
	"""Create the Fiscal Year covering `year` (calendar year, Jan 1 - Dec 31) if
	none already exists. Not scoped to a specific Company: an unrestricted
	Fiscal Year (empty `companies` table) already applies to every Company on
	the site, same as the setup wizard's own default, and a Royce-provisioned
	site has exactly one Company for its whole life anyway (see
	_generate_abbr's own docstring) -- there is nothing else to scope it to.

	Calendar year, not a government fiscal year -- matches how most Kenyan
	SMBs and KRA-aligned tax years actually run. Editable afterward like any
	other ERPNext master if a specific customer's accountant needs otherwise;
	this is a starting default, not a permanent constraint.

	Checks for date-range overlap, not just an exact name match -- found for
	real running this against the shared test bench (not assumed): ERPNext's
	own Fiscal Year.validate_overlap() is date-range based and throws rather
	than silently no-op if this tried to insert a colliding "2026" while
	some other Fiscal Year (a customer's own custom split, an earlier
	provisioning attempt, or -- on the shared test bench specifically --
	another test's own fixture) already covers the same or an overlapping
	range under a different name. Reusing whatever already overlaps is
	correct regardless of the reason: the actual need -- some Fiscal Year
	covering this date -- is already satisfied either way.
	"""
	name = str(year)
	year_start, year_end = f"{year}-01-01", f"{year}-12-31"

	if frappe.db.exists("Fiscal Year", name):
		return name

	overlapping = frappe.db.get_value(
		"Fiscal Year",
		{"year_start_date": ["<=", year_end], "year_end_date": [">=", year_start]},
		"name",
	)
	if overlapping:
		return overlapping

	frappe.get_doc(
		{
			"doctype": "Fiscal Year",
			"year": name,
			"year_start_date": year_start,
			"year_end_date": year_end,
		}
	).insert(ignore_permissions=True)
	return name


def _ensure_price_lists(currency: str) -> None:
	"""Creates the standard "Standard Buying"/"Standard Selling" Price Lists if
	neither already exists, in `currency`. Normally seeded by the setup wizard's
	own install_defaults() (erpnext/setup/setup_wizard/operations/install_fixtures.py)
	-- this pipeline never runs that either, same gap class as the Warehouse Type
	and Fiscal Year ones above, just not noticed until a real user opened "New Item
	Price" and found no Price List to pick from at all, not even ERPNext's own
	factory-default pair.

	Named exactly as install_defaults() names them (not translated -- this pipeline
	has no language selection step to translate them against) so a site that later
	does run a real setup wizard pass, or gets these seeded some other way, won't
	collide with a differently-cased duplicate.
	"""
	for price_list_name, buying, selling in (
		("Standard Buying", 1, 0),
		("Standard Selling", 0, 1),
	):
		if frappe.db.exists("Price List", price_list_name):
			continue
		frappe.get_doc(
			{
				"doctype": "Price List",
				"price_list_name": price_list_name,
				"enabled": 1,
				"buying": buying,
				"selling": selling,
				"currency": currency,
			}
		).insert(ignore_permissions=True)


def _ensure_global_defaults(company_name: str, country: str, currency: str) -> None:
	"""Sets Global Defaults' default_company/default_currency/country -- the other
	half of install_defaults() (via its own set_global_defaults()) that this
	pipeline never runs. Two separate, real symptoms trace back to this single
	missing call, found on actual tenants rather than assumed from reading the
	wizard's code alone:

	- New Employee silently fails to save with "Company is required" -- Employee's
	  own `company` field has no doctype-level default at all (just
	  remember_last_selected_value, a per-browser localStorage thing that only
	  helps after a user has picked a company once already); the client falls back
	  to erpnext.get_default_company(), which resolves to Global Defaults.
	  default_company and finds it unset on every brand-new tenant.
	- New Price List defaults its Currency to "INR" -- Frappe's own factory
	  default, unrelated to our Kenyan customer base -- for the identical reason:
	  no Global Defaults.default_currency was ever set to override it.

	Also flips `enabled` on the Currency doctype record for `currency` itself
	(install_defaults() does the same) -- an unenabled Currency is filtered out of
	every Currency Link field's own dropdown, so without this a user could type
	the right code and still not find it as a selectable option.
	"""
	frappe.db.set_value("Currency", currency, "enabled", 1)

	global_defaults = frappe.get_single("Global Defaults")
	global_defaults.default_company = company_name
	global_defaults.default_currency = currency
	global_defaults.country = country
	global_defaults.save(ignore_permissions=True)


def _ensure_genders() -> None:
	"""Seeds the standard Gender records if none exist yet. Not an erpnext or hrms
	fixture at all -- Gender is a frappe-core doctype, normally seeded by frappe
	core's own setup-wizard fixture installer (a separate module from erpnext's,
	which is the only one provision_company() calls above), so this pipeline
	skipping the wizard leaves it empty here too. Found the same way as the other
	gaps in this file: Employee.gender is `reqd: 1` with an empty options list to
	pick from, so the very first Employee a customer tries to create has no valid
	value to select at all.

	Male/Female/Other only -- the three values every Frappe version is known to
	ship, seeded directly rather than by calling frappe core's own installer
	(unlike the erpnext one above, its exact signature isn't something this app
	pins a dependency on or has verified against). If a specific site's frappe
	version ships a longer canonical list, extending this tuple is safe and
	additive -- it never removes or renames a record, so nothing an already-live
	tenant is using would break.
	"""
	for gender in ("Male", "Female", "Other"):
		if frappe.db.exists("Gender", gender):
			continue
		frappe.get_doc({"doctype": "Gender", "gender": gender}).insert(ignore_permissions=True)


def provision_company(company_name: str, country: str = "Kenya", currency: str = "KES") -> str:
	"""Creates the Company if it doesn't already exist, and returns its name
	either way. country/currency default to Kenya/KES -- every Royce Kenya
	tenant is Kenyan by definition; the parameters exist for tests, not because
	a real caller is expected to override them.

	Also runs ERPNext's own setup-wizard fixture installer first, on a genuinely
	fresh site -- found by testing an actual brand-new site, not assumed: Company's
	own on_update hook (create_default_warehouses) unconditionally expects a
	"Transit" Warehouse Type to already exist, and that record (along with a batch
	of other ERPNext preset master data -- Designations, Sales Stages, UOMs, ...)
	is normally seeded by the setup wizard's own install_fixtures.install(), which
	this platform's fully automated provisioning never runs (there is no human to
	answer it). A site with no Company yet is exactly a site that also never went
	through the wizard, so this is the right place to run it once instead of
	patching around the one symptom (the missing Warehouse Type) that happened to
	surface first.

	And, once the Company exists: tells Frappe the wizard is done, so a real
	customer logging in for the first time lands on their desk, not the wizard
	itself. Found by an actual customer signing up for real and landing on
	/desk/setup-wizard/0 instead: frappe.is_setup_complete() (which the desk uses
	to decide whether to redirect there) does not care whether a Company exists at
	all -- it only checks a separate `Installed Application.is_setup_complete` flag
	per app, which the wizard's own completion handler sets and nothing else does.
	Creating a Company by hand, however completely, never touches that flag.

	Also clears frappe's cache after setting that flag -- found by a second real
	customer, on a genuinely fresh tenant, landing correctly on /desk (not the
	wizard) but with it endlessly reloading itself every ~1s instead. Root cause:
	frappe.db.set_value() is a raw SQL write -- it never invalidates
	frappe.client_cache, a separate Redis-backed cache frappe.is_setup_complete()
	doesn't use (it queries fresh) but frappe.boot.get_bootinfo() does, via
	get_setup_wizard_completed_apps()'s frappe.client_cache.get_doc("Installed
	Applications"). So the server-side redirect check was already correct, but
	every /desk page load kept shipping the *client* stale bootinfo still saying
	frappe/erpnext hadn't finished their wizard -- which is what sent the desk's
	own JS back into wizard-init logic (visible in nginx's access log as a
	repeating setup_wizard.load_languages call) in a loop. frappe's own
	wizard-completion pipeline (update_global_settings/run_post_setup_complete in
	setup_wizard.py) always calls this right after setting the same flag, for
	this exact reason -- the minimal enable_setup_wizard_complete()-only fix
	above just didn't call it too.

	And resets the "desktop:home_page" site default -- the actual, complete
	explanation for that same reload loop, found on a *third* real customer's
	fresh tenant after the cache fix above turned out not to be sufficient by
	itself (its loop had genuinely stopped once, right when the cache fix was
	first tested live -- coincidentally, it turned out, since a brand-new tenant
	hit the identical symptom again afterwards). frappe.utils.install seeds every
	new site with frappe.db.set_default("desktop:home_page", "setup-wizard")
	unconditionally at `bench new-site` time, anticipating a human going through
	the wizard next. Nothing else ever changes it -- the wizard's own completion
	page (setup_wizard.py) is what resets it to "workspace", but that's part of
	the full process_setup_stages pipeline this function deliberately doesn't
	run. Left alone, every /desk boot keeps reporting home_page: "setup-wizard"
	forever; the setup-wizard page's own on_page_load handler sees
	frappe.boot.setup_complete is truthy, does `window.location.href = "/desk"`
	to leave -- and the next load reports the same wrong home_page again.
	Confirmed live against the actual stuck tenant, isolating this from the
	cache fix above: fetching its real boot info (an authenticated request, not
	guessed) showed "home_page":"setup-wizard" even with setup_complete already
	true; setting the default to "workspace" and re-fetching flipped it to
	"desktop" and the reload cycle -- visible until then in nginx's access log,
	repeating every ~1-2s -- did not resume.
	"""
	if frappe.db.exists("Company", company_name):
		return company_name

	first_company = not frappe.is_setup_complete()
	if first_company:
		from erpnext.setup.setup_wizard.operations.install_fixtures import install as install_erpnext_fixtures

		install_erpnext_fixtures(country=country)

	frappe.get_doc(
		{
			"doctype": "Company",
			"company_name": company_name,
			"abbr": _generate_abbr(company_name),
			"default_currency": currency,
			"country": country,
		}
	).insert(ignore_permissions=True)

	# Every accounting transaction (GL entries, most reports) needs an active
	# Fiscal Year covering its date -- found missing entirely on a genuinely
	# fresh site, the same category of gap as the Warehouse Type bug above:
	# this pipeline never runs the setup wizard, which is what normally seeds
	# one. Seeds both this year and next so a business signing up late in the
	# year isn't left without one the moment the calendar turns over.
	current_year = getdate().year
	_ensure_fiscal_year(current_year)
	_ensure_fiscal_year(current_year + 1)

	if first_company:
		# See the three docstrings above -- without these, a real customer's very
		# first session hits three separate dead ends: no Price List exists to
		# price an Item against, the one they create themselves defaults to INR
		# instead of KES, and their very first Employee can't be saved at all
		# ("Company is required" / no Gender to pick from).
		_ensure_price_lists(currency)
		_ensure_global_defaults(company_name, country, currency)
		_ensure_genders()

		# The exact two apps frappe.is_setup_complete() checks -- see its own
		# implementation in frappe/__init__.py. Deliberately the small, standalone
		# flag-setter (frappe/desk/page/setup_wizard/setup_wizard.py), not the
		# wizard's full completion pipeline (process_setup_stages) -- that also
		# creates a default user, applies telemetry preferences, sets language
		# defaults, etc., none of which apply to a tenant that was never going to
		# see the wizard's own UI at all.
		from frappe.desk.page.setup_wizard.setup_wizard import enable_setup_wizard_complete

		enable_setup_wizard_complete("frappe")
		enable_setup_wizard_complete("erpnext")

		# See the docstring above -- without this, the desk keeps serving stale
		# client_cache bootinfo that still claims frappe/erpnext haven't finished
		# their wizard, and reload-loops itself trying to resolve that.
		frappe.clear_cache()

		# See the docstring above -- without this, every /desk boot keeps
		# reporting home_page: "setup-wizard" (bench new-site's own default,
		# never cleared since this pipeline skips the wizard's completion page
		# that normally would), which reload-loops the desk trying to leave it.
		frappe.db.set_default("desktop:home_page", "workspace")

	return company_name


@frappe.whitelist()
def provision(company_name: str) -> dict:
	"""Full bootstrap for a brand-new tenant: create the Company (if needed), then
	run this app's own Kenyan Accountant Settings setup against it. Safe to call
	repeatedly -- provision_company() and run_setup() both are (see their own
	docstrings).

	Does not touch royce_payroll_ke or royce_talk -- those are separate apps with
	their own provisioning entry points (royce_payroll_ke.royce_payroll_ke.setup.
	provision(company), and royce_talk has none yet -- see ADR-017 in royce_ip),
	called separately by whoever orchestrates onboarding.
	"""
	company_name = provision_company(company_name)

	if frappe.db.exists("Kenyan Accountant Settings", company_name):
		settings = frappe.get_doc("Kenyan Accountant Settings", company_name)
	else:
		settings = frappe.get_doc(
			{"doctype": "Kenyan Accountant Settings", "company": company_name}
		).insert(ignore_permissions=True)

	result = settings.run_setup()
	return {"company": company_name, "kenyan_accountant_settings": settings.name, **result}
