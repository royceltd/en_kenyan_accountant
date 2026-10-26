# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""v16.6.0 adds to setup: the Non-Recoverable VAT account, 16% inclusive sales and
8% inclusive sales/purchase templates, the Standard/Reduced/Non-Claimable Item Tax
Templates, motor vehicle accounts and expense items, and a 0% Non-Recoverable row
on purchase templates. run_setup() only runs before activation, so companies set
up earlier get them here. Nothing the client already has is changed (ADR-023):
settings fields already pointing somewhere keep their value, and a purchase
template the accountant has edited doesn't get the new row."""

import frappe

from kenyan_accountant.setup.accounts import create_core_tax_accounts
from kenyan_accountant.setup.vat import (
	add_non_recoverable_row,
	create_item_tax_templates,
	create_vat_templates,
)
from kenyan_accountant.setup.vehicles import create_vehicle_accounts, create_vehicle_expense_items

NEW_FIELDS = (
	"non_recoverable_vat_account",
	"sales_vat_inclusive_template",
	"sales_reduced_vat_inclusive_template",
	"purchase_reduced_vat_inclusive_template",
)


def execute():
	for settings in frappe.get_all(
		"Kenya Accounting Settings",
		fields=["name", "company", "output_vat_account", "input_vat_account", *NEW_FIELDS],
	):
		if not (settings.output_vat_account and settings.input_vat_account):
			continue  # setup never completed for this company; run_setup() creates everything

		accounts = dict(settings)
		created = create_core_tax_accounts(settings.company)
		accounts["non_recoverable_vat_account"] = (
			settings.non_recoverable_vat_account or created["non_recoverable_vat_account"]
		)
		created.update(create_vat_templates(settings.company, accounts))

		updates = {f: created[f] for f in NEW_FIELDS if not settings.get(f) and created.get(f)}
		if updates:
			frappe.db.set_value("Kenya Accounting Settings", settings.name, updates, update_modified=False)

		item_tax_templates = create_item_tax_templates(settings.company, accounts)
		add_non_recoverable_row(settings.company, accounts)
		create_vehicle_expense_items(
			settings.company, create_vehicle_accounts(settings.company), item_tax_templates
		)
