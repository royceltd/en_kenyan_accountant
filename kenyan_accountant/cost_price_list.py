# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Cost Price List: a selling price list that follows each item's last purchase price.

For sales at cost: the owner buying from his own business as a customer, staff, a
related business. Set it on Kenya Accounting Settings and give those customers this
price list; their invoices then start at what the shop last paid.

The price is ERPNext's Item.last_purchase_rate: before VAT, per stock unit. So the
customer's invoice should add VAT on top (an exclusive template, e.g. via a Tax Rule),
and ERPNext converts to the pack unit itself (a Bag is 50 x the Kg price).

ERPNext's own "Update Existing Price List Rate" (Stock Settings) can't do this: it
rewrites whichever price list a transaction uses, so a till sale at an edited price
would overwrite the shelf price. This only ever touches the one cost price list.
"""

import frappe
from frappe import _
from frappe.utils import flt


def get_cost_price_list(company):
	if not company or not frappe.db.exists("Kenya Accounting Settings", company):
		return None
	return frappe.db.get_value("Kenya Accounting Settings", company, "cost_price_list")


def validate_cost_price_list(price_list):
	if price_list and not frappe.db.get_value("Price List", price_list, "selling"):
		frappe.throw(
			_("Cost Price List {0} must be a selling price list (tick Selling on it).").format(price_list)
		)


def sync_cost_price_list(doc, method=None):
	"""Purchase Receipt / Purchase Invoice on_submit and on_cancel. ERPNext has already
	updated Item.last_purchase_rate by now (BuyingController runs before doc_events)."""
	if doc.get("is_return"):
		return
	price_list = get_cost_price_list(doc.company)
	if not price_list:
		return
	for item_code in {d.item_code for d in doc.get("items") or [] if d.item_code}:
		item = frappe.db.get_value("Item", item_code, ["stock_uom", "last_purchase_rate"], as_dict=True)
		set_cost_price(price_list, item_code, item.stock_uom, item.last_purchase_rate)


def fill_cost_price_list(price_list):
	"""Price every item that has a last purchase rate. Returns how many were set."""
	items = frappe.get_all(
		"Item",
		filters={"last_purchase_rate": [">", 0], "disabled": 0},
		fields=["name", "stock_uom", "last_purchase_rate"],
	)
	for item in items:
		set_cost_price(price_list, item.name, item.stock_uom, item.last_purchase_rate)
	return len(items)


def set_cost_price(price_list, item_code, stock_uom, rate):
	"""One price per item, in its stock unit. A zero rate (its only purchase was
	cancelled) keeps the last known price rather than pricing the item at nothing."""
	rate = flt(rate, 6)  # per stock unit: a Bag is 50 x this, so keep the decimals
	if not rate:
		return
	name = frappe.db.get_value(
		"Item Price",
		{
			"price_list": price_list,
			"item_code": item_code,
			"uom": stock_uom,
			"customer": ["is", "not set"],
			"batch_no": ["is", "not set"],
		},
	)
	if name:
		if flt(frappe.db.get_value("Item Price", name, "price_list_rate"), 6) != rate:
			frappe.db.set_value("Item Price", name, "price_list_rate", rate)
		return
	frappe.get_doc(
		{
			"doctype": "Item Price",
			"price_list": price_list,
			"item_code": item_code,
			"uom": stock_uom,
			"price_list_rate": rate,
		}
	).insert(ignore_permissions=True)
