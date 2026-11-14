# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Petty cash: staff request money, the accounts office approves it, and the
payout is booked from the petty cash float to the expense accounts.

- Request: Petty Cash Request (any staff member; for themselves or on
  someone's behalf), submitted for approval.
- Approve / Reject: the accounts office (Accounts User, Accounts Manager).
  Nobody approves their own request. There is no amount limit.
- Pay: a Journal Entry (Cash Entry) debiting each line's expense account
  (and cost centre) and crediting the petty cash account; refused when the
  float holds less than the request. Cancelling that entry puts the request
  back to Approved.
- Top Up: a Journal Entry from a bank (or M-Pesa) account into petty cash.

The petty cash account is Kenya Accounting Settings > Petty Cash Account; if
empty, a "Petty Cash" ledger (type Cash) is made beside the company's default
cash account the first time it is needed and saved there. Like everything
setup makes, it is the client's afterwards (ADR-023): found by name, never
changed."""

import frappe
from frappe import _
from frappe.utils import flt, now_datetime, nowdate

APPROVER_ROLES = ("Accounts User", "Accounts Manager", "System Manager")
PETTY_CASH = "Petty Cash"


def _is_approver(user=None):
	return bool(set(APPROVER_ROLES) & set(frappe.get_roles(user or frappe.session.user)))


def _require_approver():
	if not _is_approver():
		frappe.throw(_("Only the accounts office approves and pays petty cash."), frappe.PermissionError)


def default_expense_account(item, company):
	"""The item's expense account for the company, else its item group's."""
	account = frappe.db.get_value("Item Default", {"parent": item, "company": company}, "expense_account")
	if not account:
		group = frappe.db.get_value("Item", item, "item_group")
		account = group and frappe.db.get_value(
			"Item Default", {"parent": group, "company": company}, "expense_account"
		)
	return account


def petty_cash_account(company):
	settings = frappe.db.get_value(
		"Kenya Accounting Settings", {"company": company}, ["name", "petty_cash_account"], as_dict=True
	)
	if settings and settings.petty_cash_account:
		return settings.petty_cash_account
	existing = frappe.db.get_value(
		"Account", {"account_name": PETTY_CASH, "company": company, "is_group": 0}, "name"
	)
	account = existing or _make_petty_cash_account(company)
	if settings:
		frappe.db.set_value(
			"Kenya Accounting Settings", settings.name, "petty_cash_account", account, update_modified=False
		)
	return account


def _make_petty_cash_account(company):
	from kenyan_accountant.setup.accounts import _get_root_account, get_or_create_account

	cash = frappe.get_cached_value("Company", company, "default_cash_account")
	parent = (
		(cash and frappe.db.get_value("Account", cash, "parent_account"))
		or frappe.db.get_value("Account", {"account_type": "Cash", "company": company, "is_group": 1}, "name")
		or _get_root_account(company, "Asset")
	)
	return get_or_create_account(
		company=company,
		account_name=PETTY_CASH,
		parent_account=parent,
		account_type="Cash",
		root_type="Asset",
	)


def balance(account, company):
	from erpnext.accounts.utils import get_balance_on

	return flt(get_balance_on(account=account, company=company, date=nowdate()))


@frappe.whitelist()
def summary(company: str):
	"""The float's account and balance, and whether this user approves."""
	frappe.has_permission("Petty Cash Request", "read", throw=True)
	account = petty_cash_account(company) if _is_approver() else None
	return {
		"account": account,
		"balance": balance(account, company) if account else None,
		"is_approver": _is_approver(),
	}


def _request(name, *statuses):
	doc = frappe.get_doc("Petty Cash Request", name)
	if doc.docstatus != 1 or doc.status not in statuses:
		frappe.throw(_("{0} is {1}.").format(doc.name, _(doc.status)))
	return doc


@frappe.whitelist()
def approve(name: str, remarks: str | None = None):
	_require_approver()
	doc = _request(name, "Pending Approval")
	if doc.requested_by == frappe.session.user or doc.owner == frappe.session.user:
		frappe.throw(
			_("You can't approve your own request. Another person in the accounts office approves it.")
		)
	doc.db_set(
		{
			"status": "Approved",
			"approved_by": frappe.session.user,
			"approved_on": now_datetime(),
			"approver_remarks": remarks,
		}
	)
	return doc.name


@frappe.whitelist()
def reject(name: str, remarks: str):
	_require_approver()
	if not remarks:
		frappe.throw(_("Say why the request is rejected."))
	doc = _request(name, "Pending Approval", "Approved")
	doc.db_set(
		{
			"status": "Rejected",
			"approved_by": frappe.session.user,
			"approved_on": now_datetime(),
			"approver_remarks": remarks,
		}
	)
	return doc.name


@frappe.whitelist()
def pay(name: str, received_by: str | None = None, posting_date: str | None = None):
	"""Hand over the cash: a Journal Entry from petty cash to the expenses."""
	_require_approver()
	doc = _request(name, "Approved")
	missing = [str(row.idx) for row in doc.items if not row.expense_account]
	if missing:
		frappe.throw(_("Set the expense account on row(s) {0} first.").format(", ".join(missing)))
	account = petty_cash_account(doc.company)
	available = balance(account, doc.company)
	if available + 0.005 < flt(doc.total_amount):
		frappe.throw(
			_("Petty cash ({0}) holds {1}, less than this request ({2}). Top it up first.").format(
				account,
				frappe.format(available, {"fieldtype": "Currency"}),
				frappe.format(doc.total_amount, {"fieldtype": "Currency"}),
			),
			title=_("Not enough petty cash"),
		)
	je = frappe.new_doc("Journal Entry")
	je.update(
		{
			"voucher_type": "Cash Entry",
			"company": doc.company,
			"posting_date": posting_date or nowdate(),
			"user_remark": _("Petty cash {0}: {1}").format(doc.name, doc.purpose),
		}
	)
	default_cc = doc.cost_center or frappe.get_cached_value("Company", doc.company, "cost_center")
	for row in doc.items:
		je.append(
			"accounts",
			{
				"account": row.expense_account,
				"debit_in_account_currency": flt(row.amount),
				"cost_center": row.cost_center or default_cc,
				"user_remark": row.description,
			},
		)
	je.append(
		"accounts",
		{"account": account, "credit_in_account_currency": flt(doc.total_amount), "cost_center": default_cc},
	)
	je.flags.ignore_permissions = True
	je.insert()
	je.submit()
	doc.db_set(
		{
			"status": "Paid",
			"journal_entry": je.name,
			"petty_cash_account": account,
			"paid_by": frappe.session.user,
			"paid_on": now_datetime(),
			"received_by": received_by or doc.requested_for or doc.requested_by_name,
		}
	)
	return je.name


def journal_entry_cancelled(doc, method=None):
	"""Journal Entry on_cancel: a cancelled payout puts its request back to Approved."""
	for name in frappe.get_all("Petty Cash Request", filters={"journal_entry": doc.name}, pluck="name"):
		frappe.db.set_value(
			"Petty Cash Request",
			name,
			{
				"status": "Approved",
				"journal_entry": None,
				"paid_by": None,
				"paid_on": None,
				"received_by": None,
			},
		)


@frappe.whitelist()
def top_up(
	company: str,
	from_account: str,
	amount: float,
	reference: str | None = None,
	posting_date: str | None = None,
):
	"""Refill the float from a bank or M-Pesa account."""
	_require_approver()
	amount = flt(amount)
	if amount <= 0:
		frappe.throw(_("Enter the amount."))
	account = petty_cash_account(company)
	if from_account == account:
		frappe.throw(_("Choose the bank or M-Pesa account the money comes from."))
	je = frappe.new_doc("Journal Entry")
	je.update(
		{
			"voucher_type": "Bank Entry",
			"company": company,
			"posting_date": posting_date or nowdate(),
			"cheque_no": reference or _("Petty cash top-up"),
			"cheque_date": posting_date or nowdate(),
			"user_remark": _("Petty cash top-up"),
			"accounts": [
				{"account": account, "debit_in_account_currency": amount},
				{"account": from_account, "credit_in_account_currency": amount},
			],
		}
	)
	je.flags.ignore_permissions = True
	je.insert()
	je.submit()
	return {"journal_entry": je.name, "balance": balance(account, company)}
