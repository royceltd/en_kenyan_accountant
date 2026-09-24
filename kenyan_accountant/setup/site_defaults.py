# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""The rest of the setup wizard this platform skips, plus Kenya payment defaults.

provision.py already runs ERPNext's master-data fixtures and sets the wizard's
"complete" flags, but a live audit of real tenants on 2026-09-24 found three more
wizard steps that had never run on any of them:

- frappe's update_system_settings(): System Settings.time_zone was NULL, so every
  user fell back to frappe's hardcoded "Asia/Kolkata" (frappe/utils/data.py) and
  every timestamp was 2.5h off Kenya time. Country, currency, language and date
  format were blank too, and **enable_scheduler stayed 0**, so no scheduled job
  (email queue flush, Auto Repeat, reminders, leave allocation, ...) had ever run.
- frappe's own install_fixtures.install(): Salutations (only 1 existed), Genders,
  Email Unsubscribe.
- erpnext's update_stock_settings(): default warehouse, stock UOM, and the rest.

This module calls those real functions instead of hand-copying them, so they
track upstream. It also adds Kenya-specific payment defaults the wizard never had:
an M-Pesa Mode of Payment with its own ledger, a placeholder Bank ledger set as
the Company's default bank account, and INR disabled.

Two entry points:
- apply_wizard_defaults(company): new sites, called once from provision_company().
  It uses wizard semantics and sets everything.
- backfill_site_defaults(enable_scheduler): existing, already-live sites. It only
  fills what's blank and never overwrites a value a customer may have chosen.
"""

import frappe

KENYA_WIZARD_ARGS = {
	"country": "Kenya",
	"timezone": "Africa/Nairobi",
	"currency": "KES",
	"language": "English",
}

# frappe's own `bench new-site` default -- see backfill_site_defaults() for why this
# one specific value is treated as "never chosen" rather than a customer's choice.
FRAPPE_INSTALL_DATE_FORMAT = "yyyy-mm-dd"
KENYA_DATE_FORMAT = "dd-mm-yyyy"

# frappe/utils/data.py's get_system_timezone() fallback when System Settings.time_zone
# is blank. A user carrying it on a Kenyan tenant got it by accident, not by choice.
FRAPPE_FALLBACK_TIMEZONE = "Asia/Kolkata"

MPESA_MODE_OF_PAYMENT = "M-Pesa"
MPESA_ACCOUNT_NAME = "M-Pesa"
BANK_ACCOUNT_NAME = "Bank"
# Existing ERPNext Modes of Payment that settle through a bank, not cash.
BANK_SETTLED_MODES = ("Wire Transfer", "Bank Draft", "Cheque", "Credit Card")

# (doctype, fieldname) pairs that would mean INR is genuinely in use on a site, so
# disabling it would hide a currency real records depend on.
CURRENCY_USAGE_FIELDS = (
	("Company", "default_currency"),
	("Account", "account_currency"),
	("Price List", "currency"),
	("GL Entry", "account_currency"),
	("GL Entry", "transaction_currency"),
	("Customer", "default_currency"),
	("Supplier", "default_currency"),
	("Quotation", "currency"),
	("Sales Order", "currency"),
	("Sales Invoice", "currency"),
	("Purchase Order", "currency"),
	("Purchase Invoice", "currency"),
	("Journal Entry Account", "account_currency"),
	("Payment Entry", "paid_from_account_currency"),
	("Payment Entry", "paid_to_account_currency"),
)


def apply_wizard_defaults(company: str) -> None:
	"""New-site path: exactly what the wizard would have done, Kenya-flavoured.

	Must run after the Company exists, since update_stock_settings() looks up
	the "Stores" warehouse that Company creation makes."""
	from erpnext.setup.setup_wizard.operations.install_fixtures import update_stock_settings
	from frappe.desk.page.setup_wizard import install_fixtures as frappe_fixtures
	from frappe.desk.page.setup_wizard.setup_wizard import update_system_settings

	# Sets country/time_zone/currency/language/date+time+number format/precision and
	# enable_scheduler=1 (0 under frappe.in_test, by upstream design).
	update_system_settings(frappe._dict(KENYA_WIZARD_ARGS))
	_fix_user_timezones()
	frappe_fixtures.install()
	update_stock_settings()
	frappe.db.set_single_value("Stock Settings", "email_footer_address", company)
	_disable_currency_if_unused("INR")
	seed_payment_defaults(company)


def backfill_site_defaults(enable_scheduler: bool = True) -> dict:
	"""Existing-site path, run once per live site via
	`bench --site X execute kenyan_accountant.setup.backfill_site_defaults`.

	Fill-blank only: never overwrites a value that may be a customer's choice.
	Returns what it changed, so the run is auditable from the command's output.

	enable_scheduler=False is for the public demo site, which must never send
	email. It leaves the scheduler exactly as it is (it doesn't disable it)."""
	from frappe.desk.page.setup_wizard import install_fixtures as frappe_fixtures
	from frappe.utils.scheduler import enable_scheduler as _enable_scheduler

	changes = {}

	system_settings = frappe.get_single("System Settings")
	for fieldname, value in (
		("country", KENYA_WIZARD_ARGS["country"]),
		("time_zone", KENYA_WIZARD_ARGS["timezone"]),
		("currency", KENYA_WIZARD_ARGS["currency"]),
		("language", "en"),
		("float_precision", "3"),
	):
		if not system_settings.get(fieldname):
			system_settings.set(fieldname, value)
			changes[f"System Settings.{fieldname}"] = value
	# yyyy-mm-dd is frappe's own install default, and none of these sites ever
	# went through a wizard that would have asked. Treated as "never chosen".
	if system_settings.date_format == FRAPPE_INSTALL_DATE_FORMAT:
		system_settings.date_format = KENYA_DATE_FORMAT
		changes["System Settings.date_format"] = KENYA_DATE_FORMAT
	# Deliberately NOT touched: rounding_method. Changing it on a site that already
	# has transactions changes how future totals round against past ones.
	if changes:
		system_settings.flags.ignore_permissions = True
		system_settings.save()

	fixed_users = _fix_user_timezones()
	if fixed_users:
		changes["User.time_zone"] = fixed_users

	frappe_fixtures.install()  # insert(ignore_if_duplicate=True) throughout

	# Before stock settings: the default warehouse is looked up by default company.
	changes.update(_backfill_global_defaults())
	changes.update(_backfill_stock_settings())

	if _disable_currency_if_unused("INR"):
		changes["Currency.INR"] = "disabled"

	for company in frappe.get_all("Company", pluck="name"):
		seeded = seed_payment_defaults(company)
		if seeded:
			changes[f"payment_defaults[{company}]"] = seeded

	if enable_scheduler and not frappe.db.get_single_value("System Settings", "enable_scheduler"):
		_enable_scheduler()
		changes["System Settings.enable_scheduler"] = 1

	frappe.db.commit()
	frappe.clear_cache()
	return changes


def _fix_user_timezones() -> list:
	"""Moves System Users off the accidental Asia/Kolkata fallback (or blank) onto
	the site's real time zone. Sets both the field and the per-user default,
	since User.on_update keeps the two in sync and boot reads the default."""
	target = frappe.db.get_single_value("System Settings", "time_zone") or KENYA_WIZARD_ARGS["timezone"]
	fixed = []
	for user, time_zone in frappe.get_all(
		"User", filters={"user_type": "System User"}, fields=["name", "time_zone"], as_list=True
	):
		if time_zone and time_zone != FRAPPE_FALLBACK_TIMEZONE:
			continue  # a real choice (or already fixed) -- leave it
		frappe.db.set_value("User", user, "time_zone", target, update_modified=False)
		frappe.defaults.set_default("time_zone", target, user)
		fixed.append(user)
	return fixed


def _backfill_global_defaults() -> dict:
	"""Fill-blank Global Defaults for tenants provisioned before provision.py's
	_ensure_global_defaults() existed. Found on a real staging tenant: no
	default_company, which breaks "Company is required" on new Employees and
	leaves no company to find a default warehouse for. Only acts when the site has
	exactly one Company. With more than one, which is "the default" is a human call."""
	companies = frappe.get_all("Company", pluck="name")
	if len(companies) != 1:
		return {}
	company = companies[0]
	company_currency = frappe.get_cached_value("Company", company, "default_currency")
	changes = {}
	global_defaults = frappe.get_single("Global Defaults")
	# ERPNext's factory default_currency is INR. On a KES company it's an accident,
	# not a choice (the same "Price List defaults to INR" bug provision.py fixes for
	# new sites), and every Global Defaults save re-enables it.
	if global_defaults.default_currency == "INR" and company_currency and company_currency != "INR":
		global_defaults.default_currency = None
	for fieldname, value in (
		("default_company", company),
		("default_currency", company_currency),
		("country", frappe.get_cached_value("Company", company, "country")),
	):
		if value and not global_defaults.get(fieldname):
			global_defaults.set(fieldname, value)
			changes[f"Global Defaults.{fieldname}"] = value
	if changes:
		global_defaults.save(ignore_permissions=True)
	return changes


def _backfill_stock_settings() -> dict:
	"""Fill-blank subset of erpnext's update_stock_settings(). The boolean flags it
	also sets (auto_indent, auto_insert_price_list_rate_if_missing) are left alone
	on existing sites: 0 is indistinguishable from a deliberate choice, and
	auto_indent in particular would start raising Material Requests daily once
	the scheduler is on."""
	changes = {}
	stock_settings = frappe.get_single("Stock Settings")
	if not stock_settings.default_warehouse:
		default_company = frappe.db.get_single_value("Global Defaults", "default_company")
		warehouse = frappe.db.get_value(
			"Warehouse", {"warehouse_name": "Stores", "company": default_company, "is_group": 0}
		)
		if warehouse:
			stock_settings.default_warehouse = warehouse
			changes["Stock Settings.default_warehouse"] = warehouse
	if not stock_settings.stock_uom and frappe.db.exists("UOM", "Nos"):
		stock_settings.stock_uom = "Nos"
		changes["Stock Settings.stock_uom"] = "Nos"
	if changes:
		stock_settings.flags.ignore_permissions = True
		stock_settings.save()
	return changes


def _disable_currency_if_unused(currency: str) -> bool:
	"""Disables `currency` unless a real record uses it. Returns True only when it
	actually flipped it."""
	if not frappe.db.get_value("Currency", currency, "enabled"):
		return False
	for doctype, fieldname in CURRENCY_USAGE_FIELDS:
		if not frappe.db.table_exists(doctype) or not frappe.get_meta(doctype).has_field(fieldname):
			continue
		if frappe.db.exists(doctype, {fieldname: currency}):
			return False
	frappe.db.set_value("Currency", currency, "enabled", 0)
	return True


def seed_payment_defaults(company: str) -> list:
	"""Kenya payment defaults for `company`, safe to call any number of times:

	- A "Bank" ledger (account type Bank, under Bank Accounts), set as the
	  Company's default bank account only if none is set yet. A placeholder the
	  customer renames to their real bank.
	- An "M-Pesa" ledger (account type Bank). An M-Pesa paybill/till float
	  behaves like a bank balance, not petty cash. It gets an "M-Pesa" Mode of
	  Payment (type Phone) pointing at it.
	- Default accounts for ERPNext's stock Modes of Payment that have none for
	  this company yet: bank-settled ones point at the Bank ledger, Cash at the
	  Company's own default cash account.

	Never renames, re-parents or re-points anything that already exists.
	Returns a list of what it created/set (empty on a no-op re-run)."""
	done = []
	bank_group = _get_bank_group(company)

	bank_account = _ensure_ledger(company, BANK_ACCOUNT_NAME, bank_group, done)
	mpesa_account = _ensure_ledger(company, MPESA_ACCOUNT_NAME, bank_group, done)

	if not frappe.get_cached_value("Company", company, "default_bank_account"):
		frappe.db.set_value("Company", company, "default_bank_account", bank_account)
		frappe.clear_document_cache("Company", company)
		done.append(f"Company.default_bank_account={bank_account}")

	if not frappe.db.exists("Mode of Payment", MPESA_MODE_OF_PAYMENT):
		frappe.get_doc(
			{"doctype": "Mode of Payment", "mode_of_payment": MPESA_MODE_OF_PAYMENT, "type": "Phone", "enabled": 1}
		).insert(ignore_permissions=True)
		done.append(f"Mode of Payment {MPESA_MODE_OF_PAYMENT}")

	cash_account = frappe.get_cached_value("Company", company, "default_cash_account")
	mode_defaults = {MPESA_MODE_OF_PAYMENT: mpesa_account, "Cash": cash_account}
	mode_defaults.update({mode: bank_account for mode in BANK_SETTLED_MODES})
	for mode, account in mode_defaults.items():
		if account and _set_mode_default_account(mode, company, account):
			done.append(f"Mode of Payment {mode} -> {account}")

	return done


def _ensure_ledger(company: str, account_name: str, parent: str, done: list) -> str:
	"""A Bank-type leaf ledger, reused as-is if one with this name exists. Its
	currency defaults to the Company's (Account.validate does that)."""
	from kenyan_accountant.setup.accounts import get_or_create_account

	existed = frappe.db.exists("Account", {"account_name": account_name, "company": company})
	name = get_or_create_account(
		company=company,
		account_name=account_name,
		parent_account=parent,
		account_type="Bank",
		root_type="Asset",
	)
	if not existed:
		done.append(f"Account {name}")
	return name


def _get_bank_group(company: str) -> str:
	"""The Standard chart's "Bank Accounts" group if present, else any Bank-type
	group, else a new "Bank Accounts" group under Current Assets (or the Asset
	root). Detects the existing structure rather than creating a duplicate root,
	the same rule accounts.py follows."""
	from kenyan_accountant.setup.accounts import _get_root_account, get_or_create_account

	for filters in (
		{"account_name": "Bank Accounts", "company": company, "is_group": 1},
		{"account_type": "Bank", "company": company, "is_group": 1},
	):
		existing = frappe.db.get_value("Account", filters, "name")
		if existing:
			return existing

	parent = frappe.db.get_value(
		"Account", {"account_name": "Current Assets", "company": company, "is_group": 1}, "name"
	) or _get_root_account(company, "Asset")
	return get_or_create_account(
		company=company,
		account_name="Bank Accounts",
		parent_account=parent,
		account_type="Bank",
		root_type="Asset",
		is_group=1,
	)


def _set_mode_default_account(mode: str, company: str, account: str) -> bool:
	"""Adds a company->account row to a Mode of Payment only if the mode exists and
	has no row for this company yet. Returns True if it added one."""
	if not frappe.db.exists("Mode of Payment", mode):
		return False
	if frappe.db.exists("Mode of Payment Account", {"parent": mode, "company": company}):
		return False
	doc = frappe.get_doc("Mode of Payment", mode)
	doc.append("accounts", {"company": company, "default_account": account})
	doc.save(ignore_permissions=True)
	return True
