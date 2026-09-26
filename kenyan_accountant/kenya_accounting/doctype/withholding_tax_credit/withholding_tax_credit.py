# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class WithholdingTaxCredit(Document):
	"""Pure record-keeping -- always created by
	kenyan_accountant.setup.withholding.sync_withholding_tax_credits(), never by
	hand. See that module's docstring for why: the source of truth is always a
	Payment Entry's own deductions table, this doctype only makes it visible and
	queryable (which certificates are still unclaimed) instead of buried in a
	free-text description field.
	"""

	def before_insert(self):
		if not self.party_type:
			self.party_type = "Supplier" if self.direction == "Withheld By Us (Agent)" else "Customer"


@frappe.whitelist()
def outstanding_credits(company=None):
	"""Credits not yet marked claimed -- the whole point of tracking these
	instead of leaving certificate numbers in a Payment Entry remark: money a
	customer forgot to claim on a return is money quietly lost.
	"""
	filters = {"claimed": 0}
	if company:
		filters["company"] = company
	return frappe.get_all(
		"Withholding Tax Credit",
		filters=filters,
		fields=[
			"name", "company", "direction", "tax_type", "party_type", "party",
			"amount", "certificate_number", "certificate_date", "creation",
		],
		order_by="certificate_date asc, creation asc",
	)
