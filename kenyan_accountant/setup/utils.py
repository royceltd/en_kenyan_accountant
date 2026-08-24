# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Test-site bootstrap. Pattern borrowed from royce_etims's before_tests (itself
borrowed from csf_ke): Kenyan Accountant Settings Links to Company, so tests need
a real Company - and a real Chart of Accounts - to exist, not a mocked one.

Checks for TEST_COMPANY specifically rather than "any Company exists" (royce_etims's
version checks the latter) - this app's tests hardcode TEST_COMPANY by name, so it
must exist even when run on a site that already has other, unrelated companies.
"""

import frappe
from erpnext.setup.utils import enable_all_roles_and_domains
from frappe.utils import now_datetime

TEST_COMPANY = "Kenyan Accountant Test Co"


def before_tests():
	frappe.clear_cache()

	if not frappe.db.exists("Company", TEST_COMPANY):
		from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

		year = now_datetime().year
		setup_complete(
			{
				"currency": "KES",
				"full_name": "Test User",
				"company_name": TEST_COMPANY,
				"timezone": "Africa/Nairobi",
				"company_abbr": "KATC",
				"industry": "Software",
				"country": "Kenya",
				"fy_start_date": f"{year}-01-01",
				"fy_end_date": f"{year}-12-31",
				"language": "english",
				"company_tagline": "Testing",
				"email": "test@roycetechnologies.co.ke",
				"password": "test",
				"chart_of_accounts": "Standard",
			}
		)

	enable_all_roles_and_domains()
	frappe.db.commit()  # nosemgrep
