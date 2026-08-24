# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Idempotent creation of the core Kenya VAT/WHT accounts for a Company.

Spec: "Kenyan Accountant - Accounts, VAT, WHT & Tax Configuration Specification",
sections 4-5. Two design principles from that spec drive everything here:

- Section 4: "the installer should detect the structure rather than blindly
  create duplicate roots" - so we look for the Kenya-relevant group accounts
  ERPNext's own Standard Chart of Accounts already creates (Tax Assets, Duties
  and Taxes) before creating anything ourselves.
- Section 23: the installer "must be safe to run more than once" - an account
  is identified by its (company, account_name) pair, the same pair ERPNext
  itself allows only one of. An existing account is reused untouched; nothing
  is ever renamed or overwritten by re-running setup.
"""

import frappe
from frappe import _

# Each entry maps 1:1 onto a Kenyan Accountant Settings fieldname. group_candidates
# are tried in order against the company's existing chart before falling back to
# creating a dedicated "VAT & WHT" group under the relevant root.
CORE_TAX_ACCOUNTS = [
	{
		"fieldname": "input_vat_account",
		"account_name": "Input VAT",
		"root_type": "Asset",
		"group_candidates": ["Tax Assets", "Duties and Taxes"],
		"balance_must_be": "Debit",
	},
	{
		"fieldname": "output_vat_account",
		"account_name": "Output VAT",
		"root_type": "Liability",
		"group_candidates": ["Duties and Taxes", "Tax Liabilities"],
		"balance_must_be": "Credit",
	},
	{
		"fieldname": "vat_payable_account",
		"account_name": "VAT Payable to KRA",
		"root_type": "Liability",
		"group_candidates": ["Duties and Taxes", "Tax Liabilities"],
		"balance_must_be": "Credit",
	},
	{
		"fieldname": "vat_credit_account",
		"account_name": "VAT Credit / Recoverable",
		"root_type": "Asset",
		"group_candidates": ["Tax Assets", "Duties and Taxes"],
		"balance_must_be": "Debit",
	},
	{
		"fieldname": "wht_receivable_account",
		"account_name": "WHT Receivable",
		"root_type": "Asset",
		"group_candidates": ["Tax Assets", "Duties and Taxes"],
		"balance_must_be": "Debit",
	},
	{
		"fieldname": "wht_payable_account",
		"account_name": "WHT Payable to KRA",
		"root_type": "Liability",
		"group_candidates": ["Duties and Taxes", "Tax Liabilities"],
		"balance_must_be": "Credit",
	},
]

FALLBACK_GROUP_NAME = "VAT & WHT"


def create_core_tax_accounts(company):
	"""Create (or find) the 6 core Kenya VAT/WHT accounts for `company`.

	Returns {fieldname: account_name}, keyed to match Kenyan Accountant Settings
	fields exactly, so callers can merge the result straight onto the settings doc.
	"""
	result = {}
	for spec in CORE_TAX_ACCOUNTS:
		parent = _get_or_create_group(company, spec["group_candidates"], spec["root_type"])
		result[spec["fieldname"]] = get_or_create_account(
			company=company,
			account_name=spec["account_name"],
			parent_account=parent,
			account_type="Tax",
			root_type=spec["root_type"],
			balance_must_be=spec["balance_must_be"],
		)
	return result


def get_or_create_account(
	company, account_name, parent_account, account_type=None, root_type=None, balance_must_be=None, is_group=0
):
	"""Stable-identity account lookup: (company, account_name) is treated as the
	key. If it already exists it is returned as-is - type/parent are never
	rewritten, so a manually recategorised account is left alone.
	"""
	existing = frappe.db.get_value("Account", {"account_name": account_name, "company": company}, "name")
	if existing:
		return existing

	doc = frappe.get_doc(
		{
			"doctype": "Account",
			"account_name": account_name,
			"company": company,
			"parent_account": parent_account,
			"is_group": is_group,
			"account_type": account_type,
			"root_type": root_type,
			"balance_must_be": balance_must_be,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _get_or_create_group(company, candidate_names, root_type):
	for name in candidate_names:
		existing = frappe.db.get_value(
			"Account",
			{"account_name": name, "company": company, "is_group": 1, "root_type": root_type},
			"name",
		)
		if existing:
			return existing

	# None of the known Kenya-relevant groups exist on this company's chart -
	# create a dedicated group under the root rather than guessing at one.
	root = _get_root_account(company, root_type)
	return get_or_create_account(
		company=company,
		account_name=FALLBACK_GROUP_NAME,
		parent_account=root,
		root_type=root_type,
		is_group=1,
	)


def _get_root_account(company, root_type):
	root = frappe.db.get_value(
		"Account",
		{"company": company, "root_type": root_type, "is_group": 1, "parent_account": ("is", "not set")},
		"name",
	)
	if not root:
		frappe.throw(
			_("No root {0} account found for company {1}. Set up a Chart of Accounts first.").format(
				root_type, company
			)
		)
	return root
