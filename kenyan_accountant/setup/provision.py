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
	a real caller is expected to override them."""
	if frappe.db.exists("Company", company_name):
		return company_name

	frappe.get_doc(
		{
			"doctype": "Company",
			"company_name": company_name,
			"abbr": _generate_abbr(company_name),
			"default_currency": currency,
			"country": country,
		}
	).insert(ignore_permissions=True)
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
