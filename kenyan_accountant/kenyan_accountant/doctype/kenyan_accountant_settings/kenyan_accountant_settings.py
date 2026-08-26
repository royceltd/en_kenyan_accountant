# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from kenyan_accountant.setup.accounts import create_core_tax_accounts
from kenyan_accountant.setup.vat import (
	create_tax_categories,
	create_vat_templates,
	disable_erpnext_default_kenya_tax_templates,
)
from kenyan_accountant.setup.wht import create_wht_categories

# Kept in one place because run_setup() both reads and writes these fields by
# name - the accounts.py spec list is the source of truth for what they mean.
ACCOUNT_FIELDS = [
	"input_vat_account",
	"output_vat_account",
	"vat_payable_account",
	"vat_credit_account",
	"wht_receivable_account",
	"wht_payable_account",
]


class KenyanAccountantSettings(Document):
	def validate(self):
		# "Reviewed" and "Activated" are earned states, not just labels - ticking
		# the box (and saving) is what promotes Draft Configuration -> Reviewed;
		# unticking it demotes back down rather than leaving a stale claim.
		if self.reviewed and self.setup_status == "Draft Configuration":
			self.setup_status = "Reviewed"
		elif not self.reviewed and self.setup_status in ("Reviewed", "Activated"):
			self.setup_status = "Draft Configuration"

	@frappe.whitelist()
	def run_setup(self):
		"""Create (or find) this company's core VAT/WHT accounts, VAT templates,
		Tax Categories and WHT categories. Safe to call repeatedly: an account,
		template or category the accountant has already pointed this doc at is
		never replaced - see spec section 23 (idempotency) and section 22
		(configuration is app-created but accountant-owned).
		"""
		created_accounts = create_core_tax_accounts(self.company)
		accounts = {}
		for fieldname in ACCOUNT_FIELDS:
			accounts[fieldname] = self.get(fieldname) or created_accounts[fieldname]
			if not self.get(fieldname):
				self.set(fieldname, created_accounts[fieldname])

		create_tax_categories()

		created_templates = create_vat_templates(self.company, accounts)
		for fieldname, template in created_templates.items():
			if not self.get(fieldname):
				self.set(fieldname, template)

		create_wht_categories(self.company, accounts["wht_payable_account"])

		# ERPNext's own country-default "Kenya Tax" template, if this company has
		# one, competes with the templates just created above - disable it so
		# the accountant isn't left choosing between two "defaults".
		disabled_defaults = disable_erpnext_default_kenya_tax_templates(self.company)

		if self.setup_status == "Not Started":
			self.setup_status = "Draft Configuration"

		self.save(ignore_permissions=True)
		return {"status": self.setup_status, "disabled_defaults": disabled_defaults}

	@frappe.whitelist()
	def activate(self):
		"""Step 7/8 of the spec's setup sequence: activation is a deliberate,
		separate action from running setup - it must never happen just because
		a period ended or a page loaded (spec section 24).
		"""
		if self.setup_status != "Reviewed":
			frappe.throw(_("Tick 'Reviewed' and save before activating."))
		self.setup_status = "Activated"
		self.save(ignore_permissions=True)
		return {"status": self.setup_status}
