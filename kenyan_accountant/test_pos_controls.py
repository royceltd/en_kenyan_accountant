# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Tests for pos_controls: a till's discount/price settings are enforced on the server."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from kenyan_accountant.pos_controls import enforce_pos_discount_rules

CASHIER = ["Sales User", "Cashier"]
MANAGER = ["Sales User", "Sales Manager"]


def _sale(items=None, **kw):
	return frappe._dict({
		"doctype": "Sales Invoice", "is_pos": 1, "pos_profile": "Test Till", "is_return": 0,
		"additional_discount_percentage": 0, "discount_amount": 0,
		"items": [frappe._dict(i) for i in (items or [{"item_code": "X", "item_name": "Pipe", "price_list_rate": 100, "rate": 100}])],
		**kw,
	})


def _run(doc, roles=CASHIER, allow_discount=0, allow_rate=0):
	with patch("frappe.db.get_value", return_value=(allow_discount, allow_rate)), patch("frappe.get_roles", return_value=roles):
		enforce_pos_discount_rules(doc)


class TestPosDiscountRules(IntegrationTestCase):
	def test_plain_sale_is_allowed(self):
		_run(_sale())

	def test_whole_sale_discount_blocked_for_cashier(self):
		with self.assertRaises(frappe.ValidationError) as ctx:
			_run(_sale(additional_discount_percentage=10))
		self.assertIn("discount on the whole sale", str(ctx.exception))

	def test_discount_amount_blocked_for_cashier(self):
		with self.assertRaises(frappe.ValidationError):
			_run(_sale(discount_amount=50))

	def test_item_discount_blocked_for_cashier(self):
		with self.assertRaises(frappe.ValidationError) as ctx:
			_run(_sale([{"item_code": "X", "item_name": "Pipe", "price_list_rate": 100, "rate": 90, "discount_percentage": 10}]))
		self.assertIn("a discount on Pipe", str(ctx.exception))

	def test_lowered_price_blocked_for_cashier(self):
		with self.assertRaises(frappe.ValidationError) as ctx:
			_run(_sale([{"item_code": "X", "item_name": "Pipe", "price_list_rate": 100, "rate": 80}]))
		self.assertIn("a lower price for Pipe", str(ctx.exception))

	def test_manager_may_discount(self):
		_run(_sale(additional_discount_percentage=10), roles=MANAGER)

	def test_till_that_allows_discounts(self):
		_run(_sale(additional_discount_percentage=10), allow_discount=1, allow_rate=1)

	def test_returns_and_pricing_rules_untouched(self):
		_run(_sale(is_return=1, additional_discount_percentage=10))
		_run(_sale([{"item_code": "X", "item_name": "Pipe", "price_list_rate": 100, "rate": 90, "discount_percentage": 10, "pricing_rules": "PRLE-0001"}]))

	def test_non_till_invoice_untouched(self):
		_run(_sale(is_pos=0, additional_discount_percentage=10))

	def test_hooks_are_wired(self):
		self.assertIn("kenyan_accountant.pos_controls.enforce_pos_discount_rules",
		              frappe.get_hooks("doc_events").get("Sales Invoice", {}).get("validate", []))
		self.assertIn("public/js/pos_discount_guard.js", frappe.get_hooks("page_js").get("point-of-sale", []))
