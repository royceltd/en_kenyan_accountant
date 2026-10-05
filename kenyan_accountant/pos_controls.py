# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Enforce a till's (POS Profile's) discount and price settings on the server.

ERPNext only enforces "Allow User to Edit Discount" / "Allow User to Edit Rate" in the
browser, and not even fully: the till's "Add Discount" (a discount on the whole sale) is
shown and works regardless (erpnext pos_item_cart.js creates it visible and only ever
un-hides it). Found on a client's practice till, 2026-10-05: a cashier could discount any
sale on a till where discounts are switched off.

So: a till sale (is_pos) that carries a discount, or an item priced below its price list,
is refused unless the till allows it or the user is a manager. Returns (they copy the
original sale) and discounts applied by Pricing Rules are left alone.
"""

import frappe
from frappe import _
from frappe.utils import flt

MANAGER_ROLES = {"Sales Manager", "Accounts Manager", "System Manager"}


def enforce_pos_discount_rules(doc, method=None):
	"""Sales Invoice / POS Invoice validate."""
	if not doc.get("is_pos") or not doc.get("pos_profile") or doc.get("is_return"):
		return

	allow_discount, allow_rate = frappe.db.get_value(
		"POS Profile", doc.pos_profile, ["allow_discount_change", "allow_rate_change"]
	) or (0, 0)
	if (allow_discount and allow_rate) or MANAGER_ROLES & set(frappe.get_roles()):
		return

	problems = []
	if not allow_discount:
		if flt(doc.get("additional_discount_percentage")) or flt(doc.get("discount_amount")):
			problems.append(_("a discount on the whole sale"))
		for item in doc.get("items") or []:
			if item.get("pricing_rules"):
				continue
			if flt(item.get("discount_percentage")) or flt(item.get("discount_amount")):
				problems.append(_("a discount on {0}").format(item.item_name or item.item_code))
	if not allow_rate:
		for item in doc.get("items") or []:
			if item.get("pricing_rules") or flt(item.get("discount_percentage")) or flt(item.get("discount_amount")):
				continue  # discounts are reported above
			if flt(item.get("price_list_rate")) and flt(item.get("rate")) < flt(item.get("price_list_rate")) - 0.005:
				problems.append(_("a lower price for {0}").format(item.item_name or item.item_code))

	if problems:
		frappe.throw(
			_("This till ({0}) does not allow discounts or price changes, so this sale cannot include {1}. "
			  "Remove it, or ask a manager to make the sale.").format(doc.pos_profile, ", ".join(problems)),
			title=_("Discount not allowed"),
		)
