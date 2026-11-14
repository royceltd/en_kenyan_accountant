# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Petty cash: request, approval by the accounts office, payment from the
float, top-ups, rejection, and who sees what."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from kenyan_accountant import petty_cash

REQUESTER = "pc.requester@example.com"
APPROVER = "pc.approver@example.com"


def _user(email, roles):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
				"user_type": "System User",
			}
		).insert(ignore_permissions=True)
	user = frappe.get_doc("User", email)
	if user.user_type != "System User":
		user.user_type = "System User"
		user.save(ignore_permissions=True)
	if roles:
		user.add_roles(*roles)
	return email


class TestPettyCash(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = frappe.db.get_value("Account", {"account_type": "Bank", "is_group": 0}, "company")
		self.expense = frappe.db.get_value(
			"Account",
			{
				"company": self.company,
				"root_type": "Expense",
				"is_group": 0,
				"account_type": ["is", "not set"],
			},
			"name",
		)
		self.bank = frappe.db.get_value(
			"Account", {"company": self.company, "account_type": "Bank", "is_group": 0}, "name"
		)
		if not (self.expense and self.bank):
			self.skipTest("needs an expense account and a bank account")
		_user(REQUESTER, ["Stock User"])  # any staff role: desk users can request
		_user(APPROVER, ["Accounts User"])
		self.account = petty_cash.petty_cash_account(self.company)

	def tearDown(self):
		frappe.set_user("Administrator")

	def request(self, amount=1200, user=REQUESTER):
		frappe.set_user(user)
		doc = frappe.get_doc(
			{
				"doctype": "Petty Cash Request",
				"company": self.company,
				"purpose": "Stationery for the ward",
				"on_behalf": 1,
				"requested_for": "Ward 3 nurses",
				"items": [
					{
						"description": "Box files",
						"qty": 4,
						"rate": amount / 4,
						"expense_account": self.expense,
					},
				],
			}
		).insert()
		doc.submit()
		frappe.set_user("Administrator")
		return doc

	def float_to(self, amount):
		have = petty_cash.balance(self.account, self.company)
		if have < amount:
			frappe.set_user(APPROVER)
			petty_cash.top_up(self.company, self.bank, amount - have + 10, "TEST")
			frappe.set_user("Administrator")

	def test_request_approve_pay(self):
		doc = self.request()
		self.assertEqual(
			(doc.status, doc.total_amount, doc.requested_by), ("Pending Approval", 1200, REQUESTER)
		)
		self.assertEqual(frappe.db.get_value("Account", self.account, "account_type"), "Cash")

		frappe.set_user(REQUESTER)
		with self.assertRaises(frappe.PermissionError):
			petty_cash.approve(doc.name)  # staff don't approve
		frappe.set_user(APPROVER)
		petty_cash.approve(doc.name, "OK")
		doc.reload()
		self.assertEqual((doc.status, doc.approved_by), ("Approved", APPROVER))

		self.float_to(1200)
		before = petty_cash.balance(self.account, self.company)
		frappe.set_user(APPROVER)
		je = petty_cash.pay(doc.name, "Nurse Achieng")
		doc.reload()
		self.assertEqual((doc.status, doc.journal_entry, doc.received_by), ("Paid", je, "Nurse Achieng"))
		self.assertAlmostEqual(petty_cash.balance(self.account, self.company), before - 1200)
		rows = {
			r.account: (flt(r.debit), flt(r.credit)) for r in frappe.get_doc("Journal Entry", je).accounts
		}
		self.assertEqual(rows[self.expense], (1200, 0))
		self.assertEqual(rows[self.account], (0, 1200))

		with self.assertRaisesRegex(frappe.ValidationError, "Cancel the payment first"):
			doc.cancel()

		# Cancelling the payment puts the request back to Approved
		frappe.set_user("Administrator")
		frappe.get_doc("Journal Entry", je).cancel()
		self.assertEqual(
			frappe.db.get_value("Petty Cash Request", doc.name, ["status", "journal_entry"]),
			("Approved", None),
		)

	def test_not_enough_in_the_float(self):
		doc = self.request(amount=10**9)
		frappe.set_user(APPROVER)
		petty_cash.approve(doc.name)
		with self.assertRaisesRegex(frappe.ValidationError, "Top it up first"):
			petty_cash.pay(doc.name)

	def test_nobody_approves_their_own(self):
		doc = self.request(user=APPROVER)
		frappe.set_user(APPROVER)
		with self.assertRaisesRegex(frappe.ValidationError, "own request"):
			petty_cash.approve(doc.name)

	def test_reject_needs_a_reason(self):
		doc = self.request()
		frappe.set_user(APPROVER)
		with self.assertRaises(frappe.ValidationError):
			petty_cash.reject(doc.name, "")
		petty_cash.reject(doc.name, "Buy from the stores instead")
		self.assertEqual(frappe.db.get_value("Petty Cash Request", doc.name, "status"), "Rejected")
		with self.assertRaises(frappe.ValidationError):
			petty_cash.pay(doc.name)

	def test_staff_see_only_their_own_requests(self):
		mine = self.request()
		theirs = self.request(user=APPROVER)
		frappe.set_user(REQUESTER)
		self.assertTrue(frappe.has_permission("Petty Cash Request", "read", mine.name))
		self.assertFalse(frappe.has_permission("Petty Cash Request", "read", theirs.name))
		frappe.set_user(APPROVER)
		self.assertTrue(frappe.has_permission("Petty Cash Request", "read", mine.name))

	def test_line_without_account_is_coded_before_paying(self):
		frappe.set_user(REQUESTER)
		doc = frappe.get_doc(
			{
				"doctype": "Petty Cash Request",
				"company": self.company,
				"purpose": "Repairs",
				"items": [{"description": "Tap washer", "qty": 1, "rate": 300}],
			}
		).insert()
		doc.submit()
		frappe.set_user(APPROVER)
		petty_cash.approve(doc.name)
		with self.assertRaisesRegex(frappe.ValidationError, "expense account"):
			petty_cash.pay(doc.name)
		doc = frappe.get_doc("Petty Cash Request", doc.name)
		doc.items[0].expense_account = self.expense
		doc.save()
		self.float_to(300)
		frappe.set_user(APPROVER)
		self.assertTrue(petty_cash.pay(doc.name))
