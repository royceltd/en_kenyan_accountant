__version__ = "0.0.1"


def check_app_permission():
	"""Who sees the kenyan_accountant tile on the Frappe Apps screen.

	Accounts/VAT/WHT configuration is back-office/accounting territory - portal
	(website) users have no business here, everyone else on the desk does.
	Mirrors royce_etims.check_app_permission.
	"""
	import frappe
	from frappe.utils.user import is_website_user

	if frappe.session.user == "Administrator":
		return True

	return not is_website_user()
