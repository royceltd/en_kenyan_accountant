# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

# Re-exported so `bench execute kenyan_accountant.setup.provision --kwargs '...'`
# resolves -- frappe.get_attr() does getattr(import_module("kenyan_accountant.setup"),
# "provision"), which only finds a plain module-level name, not a submodule someone
# has to already know is named provision.py. Confirmed by trying the bare dotted path
# against a real site first and getting exactly that AttributeError, not assumed.
from kenyan_accountant.setup.provision import provision, provision_company  # noqa: F401
