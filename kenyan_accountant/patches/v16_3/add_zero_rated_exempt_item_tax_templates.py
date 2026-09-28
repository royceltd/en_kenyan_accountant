# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""v16.3.1 adds the KE Zero Rated / KE Exempt Item Tax Templates to setup. run_setup()
only runs before activation, so companies set up earlier get them here. A template
the client already has under the same title is left as it is (ADR-023)."""

import frappe

from kenyan_accountant.setup.vat import create_item_tax_templates


def execute():
	for row in frappe.get_all(
		"Kenya Accounting Settings", fields=["company", "output_vat_account", "input_vat_account"]
	):
		if not (row.output_vat_account and row.input_vat_account):
			continue  # setup never completed for this company; run_setup() creates them
		create_item_tax_templates(row.company, row)
