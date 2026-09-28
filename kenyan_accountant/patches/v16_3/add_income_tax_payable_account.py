# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""v16.3.0 adds Income Tax Payable to the core accounts (WHT a customer withheld
is claimed against it). run_setup() only runs before activation, so companies set
up earlier get it here. Only an empty setting is filled: an account the client
already chose is theirs (ADR-023), and an existing "Income Tax Payable" account
is reused, never duplicated."""

import frappe

from kenyan_accountant.setup.accounts import CORE_TAX_ACCOUNTS, _get_or_create_group, get_or_create_account


def execute():
	spec = next(s for s in CORE_TAX_ACCOUNTS if s["fieldname"] == "income_tax_payable_account")
	for row in frappe.get_all("Kenya Accounting Settings", fields=["name", "company", "income_tax_payable_account"]):
		if row.income_tax_payable_account:
			continue
		parent = _get_or_create_group(row.company, spec["group_candidates"], spec["root_type"])
		account = get_or_create_account(
			company=row.company,
			account_name=spec["account_name"],
			parent_account=parent,
			account_type="Tax",
			root_type=spec["root_type"],
			balance_must_be=spec["balance_must_be"],
		)
		# db.set_value, not doc.save(): saving re-runs validate(), which can move
		# setup_status, and a data fix must not change the client's review state.
		frappe.db.set_value("Kenya Accounting Settings", row.name, "income_tax_payable_account", account)
