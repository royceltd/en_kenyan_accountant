# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Pricing Profile: which VAT templates a company's new invoices start with.

ERPNext preselects a company's "Is Default" Sales/Purchase Taxes and Charges
Template on every new invoice, and a POS Profile carries its own template.
A profile just ticks the right defaults - so a user never has to remember to
pick a template, and an invoice can't go out with no VAT row by omission (an
invoice with an empty tax table posts no VAT at all).

The profile is a starting point, not a lock: the accountant can tick a
different default on any template afterwards, and Item Tax Templates still
override the rate per line (exempt, zero-rated, 8%, non-claimable).
"""

import frappe
from frappe import _

# Profile -> (sales template field, purchase template field) on Kenya
# Accounting Settings. Fields, not titles, so a template the accountant has
# repointed in settings is the one that becomes default.
PRICING_PROFILES = {
	# Shelf price is what the customer pays; supplier receipts are VAT-inclusive.
	"Retail / POS": ("sales_vat_inclusive_template", "purchase_vat_inclusive_template"),
	# Quotes and invoices add VAT on top.
	"B2B / Services": ("sales_vat_template", "purchase_vat_template"),
	# Pump price includes VAT, at the reduced fuel rate. Shop items at 16% carry
	# the KE Standard 16% Item Tax Template.
	"Fuel Station": ("sales_reduced_vat_inclusive_template", "purchase_reduced_vat_inclusive_template"),
}


def apply_pricing_profile(settings):
	"""Make the profile's templates the company defaults, and give POS Profiles
	with no template (or one of ours) the profile's sales template. Returns the
	{"sales": ..., "purchase": ..., "pos_profiles": [...]} that changed."""
	fields = PRICING_PROFILES.get(settings.pricing_profile)
	if not fields:
		return {}

	sales, purchase = (settings.get(f) for f in fields)
	missing = [f for f, v in zip(fields, (sales, purchase), strict=True) if not v]
	if missing:
		frappe.throw(
			_("Run Setup first - the {0} profile needs these templates: {1}").format(
				settings.pricing_profile, ", ".join(missing)
			)
		)

	_make_default("Sales Taxes and Charges Template", sales)
	_make_default("Purchase Taxes and Charges Template", purchase)
	return {"sales": sales, "purchase": purchase, "pos_profiles": _update_pos_profiles(settings, sales)}


def _make_default(doctype, name):
	"""ERPNext's own template validate() clears is_default on the company's other
	templates when one is saved as default - so save, don't db.set_value."""
	doc = frappe.get_doc(doctype, name)
	if not doc.is_default or doc.disabled:
		doc.is_default = 1
		doc.disabled = 0
		doc.save(ignore_permissions=True)


def _update_pos_profiles(settings, sales_template):
	"""A POS Profile whose template the client picked themselves is left alone;
	only empty ones and ones still on one of our KE templates follow the profile."""
	ours = {
		settings.get(f)
		for f in (
			"sales_vat_template",
			"sales_vat_inclusive_template",
			"sales_reduced_vat_inclusive_template",
		)
	} - {None, ""}
	changed = []
	for pos in frappe.get_all(
		"POS Profile", filters={"company": settings.company}, fields=["name", "taxes_and_charges"]
	):
		if pos.taxes_and_charges in (None, "", *ours) and pos.taxes_and_charges != sales_template:
			frappe.db.set_value("POS Profile", pos.name, "taxes_and_charges", sales_template)
			changed.append(pos.name)
	return changed
