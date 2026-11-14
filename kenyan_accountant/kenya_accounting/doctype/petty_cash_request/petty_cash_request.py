# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Petty Cash Request: request -> approval by the accounts office -> payment.
The workflow and the accounting are in kenyan_accountant.petty_cash."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class PettyCashRequest(Document):
	def validate(self):
		if self.is_new() or self.docstatus == 0:
			self.requested_by = self.requested_by or frappe.session.user
			self.status = "Draft"
		if not self.on_behalf:
			self.requested_for = None
		self.set_lines()

	def set_lines(self):
		from kenyan_accountant.petty_cash import default_expense_account

		total = 0
		for row in self.items:
			if flt(row.qty) <= 0 or flt(row.rate) < 0:
				frappe.throw(_("Row {0}: enter a quantity and a price.").format(row.idx))
			row.amount = flt(row.qty) * flt(row.rate)
			total += row.amount
			if row.item and not row.expense_account:
				row.expense_account = default_expense_account(row.item, self.company)
			if row.expense_account:
				company = frappe.db.get_value("Account", row.expense_account, "company")
				if company != self.company:
					frappe.throw(
						_("Row {0}: {1} is not an account of {2}.").format(
							row.idx, row.expense_account, self.company
						)
					)
		if total <= 0:
			frappe.throw(_("The request comes to nothing. Enter the amounts."))
		self.total_amount = total

	def before_update_after_submit(self):
		# the accounts office may correct accounts and cost centres before paying
		if self.status in ("Paid", "Rejected", "Cancelled"):
			frappe.throw(_("A {0} request can't be changed.").format(_(self.status).lower()))
		self.set_lines()

	def on_submit(self):
		self.db_set("status", "Pending Approval")

	def before_cancel(self):
		if self.journal_entry and frappe.db.get_value("Journal Entry", self.journal_entry, "docstatus") == 1:
			frappe.throw(
				_("This request has been paid ({0}). Cancel the payment first.").format(self.journal_entry),
				title=_("Already paid"),
			)

	def on_cancel(self):
		self.db_set("status", "Cancelled")
