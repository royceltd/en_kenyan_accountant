# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Motor vehicle accounts and ready-made expense items.

ERPNext's Standard Chart has no vehicle accounts, so an SME's fuel and garage
bills used to land in Travel or Miscellaneous Expenses with the VAT guessed.

The vehicle's class decides the VAT, so the class is part of the item: input
VAT on passenger cars and minibuses, and on their repairs and spares, can't be
claimed (VAT Act 2013 s.17(5)), while a lorry's can. Picking "Fuel - Passenger
Vehicle" or "Fuel - Commercial Vehicle" on a Purchase Invoice fills in the
expense account and the Item Tax Template, and that template routes the line's
VAT to Non-Recoverable VAT (an expense) or Input VAT (claimed). A company with
both kinds of vehicle picks per line.

Same ownership rule as the rest of setup (ADR-023): accounts are found by
(company, account_name) and items by item_code; existing ones are reused and
never rewritten. An existing item only gains rows for a company it doesn't
yet have.
"""

import frappe

from kenyan_accountant.setup.accounts import _get_or_create_group, get_or_create_account

VEHICLE_EXPENSE_GROUP = "Motor Vehicle Expenses"
VEHICLE_ITEM_GROUP = "Vehicle Expenses"

# key -> account_name, under the Motor Vehicle Expenses group.
VEHICLE_EXPENSE_ACCOUNTS = {
	"fuel": "Fuel and Oil",
	"repairs": "Vehicle Repairs and Service",
	"insurance": "Vehicle Insurance",
	"licences": "Vehicle Licences and Parking",
}

# (item_code, expense account key, Item Tax Template title or None, stock UOM)
VEHICLE_EXPENSE_ITEMS = [
	("Fuel - Passenger Vehicle", "fuel", "KE Non-Claimable 8%", "Litre"),
	("Fuel - Commercial Vehicle", "fuel", "KE Reduced 8%", "Litre"),
	("Service & Repairs - Passenger Vehicle", "repairs", "KE Non-Claimable 16%", "Nos"),
	("Service & Repairs - Commercial Vehicle", "repairs", "KE Standard 16%", "Nos"),
	# Insurance is VAT-exempt; parking, licences and inspection are government
	# fees with no VAT. Exempt keeps either from being charged the invoice's 16%.
	("Vehicle Insurance", "insurance", "KE Exempt", "Nos"),
	("Parking, Licences & Inspection", "licences", "KE Exempt", "Nos"),
]


def create_vehicle_accounts(company):
	"""Returns {"motor_vehicles": ..., "fuel": ..., "repairs": ..., ...}."""
	expenses = _get_or_create_group(company, ["Indirect Expenses"], "Expense")
	group = get_or_create_account(
		company=company,
		account_name=VEHICLE_EXPENSE_GROUP,
		parent_account=expenses,
		root_type="Expense",
		is_group=1,
	)
	result = {
		key: get_or_create_account(
			company=company, account_name=name, parent_account=group, root_type="Expense"
		)
		for key, name in VEHICLE_EXPENSE_ACCOUNTS.items()
	}
	result["motor_vehicles"] = get_or_create_account(
		company=company,
		account_name="Motor Vehicles",
		parent_account=_get_or_create_group(company, ["Fixed Assets"], "Asset"),
		account_type="Fixed Asset",
		root_type="Asset",
	)
	return result


def create_vehicle_expense_items(company, vehicle_accounts, item_tax_templates):
	"""item_tax_templates: {title: template_name} from vat.create_item_tax_templates.
	Returns the item codes created or given rows for this company."""
	_ensure_item_group()
	touched = []
	for item_code, account_key, tax_title, uom in VEHICLE_EXPENSE_ITEMS:
		expense_account = vehicle_accounts[account_key]
		tax_template = item_tax_templates.get(tax_title) if tax_title else None

		if not frappe.db.exists("Item", item_code):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": item_code,
					"item_name": item_code,
					"item_group": VEHICLE_ITEM_GROUP,
					"stock_uom": uom if frappe.db.exists("UOM", uom) else "Nos",
					"is_stock_item": 0,
					"is_purchase_item": 1,
					"is_sales_item": 0,
					"include_item_in_manufacturing": 0,
					"item_defaults": [{"company": company, "expense_account": expense_account}],
					"taxes": [{"item_tax_template": tax_template}] if tax_template else [],
				}
			).insert(ignore_permissions=True)
			touched.append(item_code)
			continue

		item = frappe.get_doc("Item", item_code)
		changed = False
		if not any(row.company == company for row in item.item_defaults):
			item.append("item_defaults", {"company": company, "expense_account": expense_account})
			changed = True
		if tax_template and not any(row.item_tax_template == tax_template for row in item.taxes):
			item.append("taxes", {"item_tax_template": tax_template})
			changed = True
		if changed:
			item.save(ignore_permissions=True)
			touched.append(item_code)
	return touched


def _ensure_item_group():
	if not frappe.db.exists("Item Group", VEHICLE_ITEM_GROUP):
		frappe.get_doc(
			{
				"doctype": "Item Group",
				"item_group_name": VEHICLE_ITEM_GROUP,
				"parent_item_group": frappe.db.get_value(
					"Item Group", {"is_group": 1, "parent_item_group": ""}
				)
				or "All Item Groups",
				"is_group": 0,
			}
		).insert(ignore_permissions=True)
