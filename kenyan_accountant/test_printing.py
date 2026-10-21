# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""The ownership rule (ADR-023 in royce_ip) for print formats and shipped records:
we set things once, a client's own choice survives every migrate after that."""

import hashlib
import os

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils.fixtures import sync_fixtures

from kenyan_accountant.patches.v16_2.remove_shipped_client_scripts import remove_if_unchanged
from kenyan_accountant.printing import (
	DEFAULT_PRINT_FORMATS,
	address_block,
	amount_box,
	document_badge,
	document_title,
	kenya_pct,
	kenya_qty,
	set_default_print_formats,
	uses_own_letter_head,
)
from kenyan_accountant.setup_checklist import pending_items

PS_FILTERS = {"doc_type": "Sales Invoice", "doctype_or_field": "DocType", "property": "default_print_format"}


def _default_for(doctype):
	return frappe.db.get_value(
		"Property Setter",
		{"doc_type": doctype, "doctype_or_field": "DocType", "property": "default_print_format"},
		"value",
	)


class TestDefaultPrintFormats(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_sets_ours_where_there_is_no_default(self):
		frappe.db.delete("Property Setter", PS_FILTERS)
		self.assertIn("Sales Invoice", set_default_print_formats())
		self.assertEqual(_default_for("Sales Invoice"), "Kenya Tax Invoice")

	def test_replaces_erpnexts_own_install_default(self):
		"""ERPNext's installer sets "... with Item Image" (16.36) or "... Classic" (16.5x)
		before our app installs, on every fresh site. That's not a client's choice."""
		for erpnext_default in ("Sales Invoice with Item Image", "Sales Invoice Classic"):
			with self.subTest(erpnext_default):
				frappe.db.delete("Property Setter", PS_FILTERS)
				frappe.make_property_setter(
					{
						"doctype": "Sales Invoice",
						"doctype_or_field": "DocType",
						"property": "default_print_format",
						"value": erpnext_default,
						"property_type": "Link",
					}
				)
				self.assertIn("Sales Invoice", set_default_print_formats())
				self.assertEqual(_default_for("Sales Invoice"), "Kenya Tax Invoice")

	def test_never_replaces_a_clients_own_default(self):
		frappe.db.delete("Property Setter", PS_FILTERS)
		# What "Set as default" on a client's own print format writes.
		frappe.make_property_setter(
			{
				"doctype": "Sales Invoice",
				"doctype_or_field": "DocType",
				"property": "default_print_format",
				"value": "Standard",
				"property_type": "Data",
			}
		)
		self.assertNotIn("Sales Invoice", set_default_print_formats())
		self.assertEqual(_default_for("Sales Invoice"), "Standard")

	def test_a_clients_default_survives_migrate(self):
		"""The actual bug: until v16.2.0 our default shipped as a fixture, and every
		migrate's fixture sync put it back over the client's choice."""
		frappe.db.delete("Property Setter", PS_FILTERS)
		frappe.make_property_setter(
			{
				"doctype": "Sales Invoice",
				"doctype_or_field": "DocType",
				"property": "default_print_format",
				"value": "Standard",
				"property_type": "Data",
			}
		)
		sync_fixtures("kenyan_accountant")
		self.assertEqual(_default_for("Sales Invoice"), "Standard")

	def test_our_formats_are_standard(self):
		for doctype, name in DEFAULT_PRINT_FORMATS.items():
			values = frappe.db.get_value("Print Format", name, ["standard", "doc_type"], as_dict=True)
			self.assertEqual(values.standard, "Yes", name)
			self.assertEqual(values.doc_type, doctype, name)


class TestLetterHead(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_erpnexts_stock_letterhead_is_not_the_clients_until_edited(self):
		name = "Company Letterhead - Grey"
		if not frappe.db.exists("Letter Head", name):
			self.skipTest("ERPNext's stock letterhead isn't on this site")
		shipped = frappe.read_file(frappe.get_app_path("erpnext", "accounts", "letterhead", "company_letterhead_grey.html"))
		frappe.db.set_value("Letter Head", name, "content", shipped)
		doc = frappe._dict(letter_head=name)
		self.assertFalse(uses_own_letter_head(doc))
		frappe.db.set_value("Letter Head", name, "content", shipped + "<p>Our own line</p>")
		self.assertTrue(uses_own_letter_head(doc))


class TestTemplatesCompile(IntegrationTestCase):
	def test_every_template_parses(self):
		base = frappe.get_app_path("kenyan_accountant", "kenya_accounting", "print_format")
		jenv = frappe.get_jenv()
		for folder in os.listdir(base):
			path = os.path.join(base, folder, f"{folder}.html")
			if os.path.exists(path):
				with open(path) as f:
					jenv.from_string(f.read())


class TestDocumentTitle(IntegrationTestCase):
	def _doc(self, doctype, **values):
		return frappe._dict(doctype=doctype, docstatus=1, **values)

	def test_sales_invoice_titles(self):
		self.assertEqual(document_title(self._doc("Sales Invoice", total_taxes_and_charges=160)), "Tax Invoice")
		self.assertEqual(document_title(self._doc("Sales Invoice", total_taxes_and_charges=0)), "Invoice")
		self.assertEqual(document_title(self._doc("Sales Invoice", is_return=1)), "Credit Note")
		self.assertEqual(document_title(self._doc("Sales Invoice", is_debit_note=1)), "Debit Note")

	def test_payment_titles(self):
		self.assertEqual(document_title(self._doc("Payment Entry", payment_type="Receive")), "Payment Receipt")
		self.assertEqual(document_title(self._doc("Payment Entry", payment_type="Pay")), "Payment Voucher")

	def test_badges(self):
		self.assertEqual(document_badge(frappe._dict(doctype="Quotation", docstatus=0)), "Draft")
		self.assertEqual(document_badge(frappe._dict(doctype="Quotation", docstatus=2)), "Cancelled")
		paid = frappe._dict(doctype="Sales Invoice", docstatus=1, grand_total=100, outstanding_amount=0)
		self.assertEqual(document_badge(paid), "Paid")

	def test_number_formatting(self):
		self.assertEqual(kenya_qty(1.0), "1")
		self.assertEqual(kenya_qty(2.5), "2.5")
		self.assertEqual(kenya_pct(16.0), "16%")
		self.assertEqual(kenya_pct(16.05), "16.05%")


class TestAddressBlock(IntegrationTestCase):
	"""Inputs are what Frappe's default address template saves on a document."""

	def test_po_box_joins_its_postal_code_and_town(self):
		block = address_block(
			"Kimathi House, Moi Avenue<br>\nP.O. Box 3456<br>Mombasa<br>\n80100<br>Kenya<br>\n<br>\n"
			"Phone: +254 733 555 222<br>Email: info@example.co.ke<br>"
		)
		self.assertEqual(block.lines, ["Kimathi House, Moi Avenue", "P.O. Box 3456-80100, Mombasa"])
		self.assertEqual(block.phone, "+254 733 555 222")
		self.assertEqual(block.email, "info@example.co.ke")

	def test_po_box_inside_a_street_line(self):
		block = address_block("Viking House<br>Waiyaki Way, Westlands. P.O. Box 45210<br>Nairobi<br>00100<br>Kenya<br>")
		self.assertEqual(block.lines, ["Viking House", "Waiyaki Way, Westlands", "P.O. Box 45210-00100, Nairobi"])

	def test_no_po_box_puts_the_postal_code_after_the_town(self):
		block = address_block("Thika Road Business Park<br>Thika<br>01000<br>Kenya<br>")
		self.assertEqual(block.lines, ["Thika Road Business Park", "Thika 01000"])

	def test_po_box_with_its_own_town_prints_as_typed(self):
		# Premises in Nairobi, post office box in Bomet: the box keeps its own town.
		block = address_block("Vision Plaza, Mombasa Road<br>\nP.O. Box 262-20400, Bomet<br>Nairobi<br>\nKenya<br>")
		self.assertEqual(block.lines, ["Vision Plaza, Mombasa Road", "Nairobi", "P.O. Box 262-20400, Bomet"])

	def test_digits_as_the_first_line_are_not_a_postal_code(self):
		block = address_block("21212<br>\nNyeri<br>\nKenya<br>\n<br>\n")
		self.assertEqual(block.lines, ["21212", "Nyeri"])

	def test_a_foreign_country_stays(self):
		block = address_block("Plot 14, Kampala Road<br>Kampala<br>Uganda<br>")
		self.assertEqual(block.lines, ["Plot 14, Kampala Road", "Kampala", "Uganda"])

	def test_empty(self):
		self.assertEqual(address_block("").lines, [])
		self.assertEqual(address_block(None).lines, [])


class TestAmountBox(IntegrationTestCase):
	def _doc(self, doctype, **values):
		return frappe._dict(doctype=doctype, currency="KES", **values)

	def test_invoice_shows_what_is_still_owed(self):
		box = amount_box(self._doc("Sales Invoice", docstatus=1, grand_total=1160, outstanding_amount=400, due_date="2026-11-19"))
		self.assertEqual((box.label, box.amount, box.note), ("Amount Due", 400, "Due by {0}"))

	def test_draft_invoice_owes_the_total(self):
		box = amount_box(self._doc("Sales Invoice", docstatus=0, grand_total=1160, outstanding_amount=0))
		self.assertEqual(box.amount, 1160)
		self.assertEqual(box.note, "")

	def test_paid_invoice(self):
		box = amount_box(self._doc("Sales Invoice", docstatus=1, grand_total=1160, outstanding_amount=0))
		self.assertEqual((box.amount, box.note), (0, "Paid in full"))

	def test_credit_note_shows_its_total(self):
		box = amount_box(self._doc("Sales Invoice", docstatus=1, is_return=1, grand_total=-580))
		self.assertEqual((box.label, box.amount), ("Credit Note Total", 580))

	def test_quotation_and_order(self):
		self.assertEqual(amount_box(self._doc("Quotation", grand_total=90, valid_till="2026-11-03")).label, "Quotation Total")
		self.assertEqual(amount_box(self._doc("Purchase Order", grand_total=90)).label, "Order Total")


class TestRemoveShippedClientScripts(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def _script(self, name, script):
		frappe.get_doc(
			{"doctype": "Client Script", "name": name, "dt": "Payment Entry", "view": "Form", "script": script}
		).insert(ignore_permissions=True, set_name=name)

	def test_removes_only_an_unchanged_copy(self):
		self._script("KA Test Unchanged", "console.log(1);")
		self._script("KA Test Edited", "console.log('edited by client');")
		shipped = {
			"KA Test Unchanged": hashlib.sha256(b"console.log(1);").hexdigest(),
			"KA Test Edited": hashlib.sha256(b"console.log(1);").hexdigest(),
		}
		self.assertEqual(remove_if_unchanged(shipped), ["KA Test Unchanged"])
		self.assertTrue(frappe.db.exists("Client Script", "KA Test Edited"))


class TestSetupChecklist(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_lists_what_is_missing_and_drops_what_is_done(self):
		company = frappe.db.get_value("Company", {}, "name")
		frappe.db.set_value("Company", company, {"tax_id": "", "phone_no": "", "email": ""})
		self.assertIn("pin", [i["key"] for i in pending_items(company)])

		frappe.db.set_value("Company", company, "tax_id", "P051234567X")
		self.assertNotIn("pin", [i["key"] for i in pending_items(company)])
