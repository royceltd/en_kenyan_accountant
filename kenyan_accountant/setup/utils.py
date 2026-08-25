# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Test-site bootstrap. Kenyan Accountant Settings Links to Company, so tests
need a real Company - and a real Chart of Accounts - to exist, not a mocked one.

Deliberately does NOT use ERPNext's setup wizard (frappe.desk.page.setup_wizard.
setup_wizard.setup_complete), unlike royce_etims/csf_ke's before_tests. That
wizard is a no-op once `frappe.is_setup_complete()` is true for the site (see
setup_wizard.py: "if frappe.is_setup_complete(): return") - which is already
the case on any site that has a real company set up, e.g. this project's own
dev site. Instead this creates the Company directly: Company's own after_insert
already builds the Standard Chart of Accounts, default cost center and
warehouses (erpnext.setup.doctype.company.company.create_default_accounts),
so nothing else needs to run the wizard for it. Works identically on a
genuinely fresh site or an already-configured one.
"""

import frappe
from erpnext.setup.utils import enable_all_roles_and_domains
from frappe.utils import now_datetime

TEST_COMPANY = "Kenyan Accountant Test Co"


def before_tests():
	frappe.clear_cache()

	if not frappe.db.exists("Company", TEST_COMPANY):
		company = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": TEST_COMPANY,
				"abbr": "KATC",
				"default_currency": "KES",
				"country": "Kenya",
			}
		)
		company.insert(ignore_permissions=True)

		year = now_datetime().year
		if not frappe.db.exists("Fiscal Year", f"KATC Test FY {year}"):
			frappe.get_doc(
				{
					"doctype": "Fiscal Year",
					"year": f"KATC Test FY {year}",
					"year_start_date": f"{year}-01-01",
					"year_end_date": f"{year}-12-31",
					"companies": [{"company": company.name}],
				}
			).insert(ignore_permissions=True)

	enable_all_roles_and_domains()
	frappe.db.commit()  # nosemgrep
