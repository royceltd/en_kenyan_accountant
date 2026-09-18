# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Provisions a named System Manager login for the customer's own contact email,
instead of handing them the shared Administrator account (Royce Control Plane's
ADR-016 credential-delivery flow).

Deliberately not `bench add-system-manager` (frappe.utils.user.add_system_manager):
that CLI command always calls `user.insert()` unconditionally and raises
DuplicateEntryError on a second call for the same email -- found for real running
this against a genuinely re-provisioned tenant, since a retried provisioning job
(ADR-014) must be able to call this again safely. This mirrors that same function's
logic (same role set, same frappe.utils.password.update_password call for a known
password) but branches on whether the user already exists first, so it's actually
idempotent -- matching every other step in the provisioning pipeline.

Housed here for the same reason kenyan_accountant.setup.provision is: every current
Royce plan includes kenyan_accountant unconditionally, so the platform-side caller
(the Royce Server Agent) always has a stable, real function to call regardless of
which other apps a given plan includes.
"""

import frappe
from frappe.permissions import AUTOMATIC_ROLES
from frappe.utils.password import update_password


def create_or_update(email: str, first_name: str, password: str) -> None:
	if frappe.db.exists("User", email):
		user = frappe.get_doc("User", email)
		if not user.enabled:
			user.enabled = 1
			user.save(ignore_permissions=True)
	else:
		user = frappe.new_doc("User")
		user.update({
			"name": email,
			"email": email,
			"enabled": 1,
			"first_name": first_name or email,
			"user_type": "System User",
			"send_welcome_email": 0,
		})
		user.insert(ignore_permissions=True)

	# Same role set add_system_manager grants -- every role except the automatic
	# ones (Guest/All), not just "System Manager" by name. add_roles is itself
	# idempotent (skips roles the user already has), so this is safe to repeat.
	roles = frappe.get_all("Role", fields=["name"], filters={"name": ["not in", AUTOMATIC_ROLES]})
	user.add_roles(*[r.name for r in roles])

	update_password(user=user.name, pwd=password)
	frappe.db.commit()
