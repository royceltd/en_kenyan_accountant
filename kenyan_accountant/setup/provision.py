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

	if first_company:
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
