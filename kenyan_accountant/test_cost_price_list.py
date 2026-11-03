# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Tests for cost_price_list: a selling price list that follows the last purchase price."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from kenyan_accountant.cost_price_list import (
	fill_cost_price_list,
	set_cost_price,
	sync_cost_price_list,
	validate_cost_price_list,
)

LIST = "_Test Cost Price List"
ITEM = "_Test Cost Item"


def _price(uom="Nos"):
	return frappe.db.get_value("Item Price", {"price_list": LIST, "item_code": ITEM, "uom": uom}, "price_list_rate")


def _purchase(**kw):
	return frappe._dict({"doctype": "Purchase Receipt", "company": "X", "is_return": 0, "items": [frappe._dict(item_code=ITEM)], **kw})


class TestCostPriceList(IntegrationTestCase):
	def setUp(self):
		if not frappe.db.exists("Price List", LIST):
			frappe.get_doc({"doctype": "Price List", "price_list_name": LIST, "selling": 1, "currency": "KES"}).insert()
		if not frappe.db.exists("Item", ITEM):
			frappe.get_doc({"doctype": "Item", "item_code": ITEM, "item_group": "All Item Groups", "stock_uom": "Nos"}).insert()
		frappe.db.set_value("Item", ITEM, "last_purchase_rate", 0)
		frappe.db.delete("Item Price", {"price_list": LIST})

	def test_creates_then_updates_one_price(self):
		set_cost_price(LIST, ITEM, "Nos", 625.86)
		set_cost_price(LIST, ITEM, "Nos", 640)
		self.assertEqual(_price(), 640)
		self.assertEqual(frappe.db.count("Item Price", {"price_list": LIST, "item_code": ITEM}), 1)

	def test_zero_rate_keeps_last_known_price(self):
		set_cost_price(LIST, ITEM, "Nos", 625.86)
		set_cost_price(LIST, ITEM, "Nos", 0)
		self.assertEqual(_price(), 625.86)

	def test_purchase_sets_price_from_last_purchase_rate(self):
		frappe.db.set_value("Item", ITEM, "last_purchase_rate", 12.5172)
		with patch("kenyan_accountant.cost_price_list.get_cost_price_list", return_value=LIST):
			sync_cost_price_list(_purchase())
		self.assertEqual(_price(), 12.5172)

	def test_return_and_unset_list_do_nothing(self):
		frappe.db.set_value("Item", ITEM, "last_purchase_rate", 50)
		with patch("kenyan_accountant.cost_price_list.get_cost_price_list", return_value=LIST):
			sync_cost_price_list(_purchase(is_return=1))
		with patch("kenyan_accountant.cost_price_list.get_cost_price_list", return_value=None):
			sync_cost_price_list(_purchase())
		self.assertIsNone(_price())

	def test_fill_prices_items_with_a_purchase_rate(self):
		frappe.db.set_value("Item", ITEM, "last_purchase_rate", 99)
		self.assertGreaterEqual(fill_cost_price_list(LIST), 1)
		self.assertEqual(_price(), 99)

	def test_buying_only_list_is_refused(self):
		frappe.db.set_value("Price List", LIST, "selling", 0)
		with self.assertRaises(frappe.ValidationError):
			validate_cost_price_list(LIST)
