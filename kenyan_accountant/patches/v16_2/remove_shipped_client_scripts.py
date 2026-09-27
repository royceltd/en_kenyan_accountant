# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

"""Until v16.2.0 the Payment Entry "Kenya Tax" buttons shipped as a Client Script
record (a fixture), re-imported on every migrate. They are app code now
(public/js/payment_entry.js via doctype_js), so the record would run the same
script twice. Removed only if it's still exactly what we shipped: a client who
edited it keeps their copy (ADR-023: nothing a client changed is overwritten)."""

import hashlib

import frappe

SHIPPED = {
	"Kenya Accounting - Payment Entry VAT Withholding": "c2b4cf684c55cee214194219692a94a553d38d6ef8e6cabaa56dabe72666bc43",
}


def execute():
	remove_if_unchanged(SHIPPED)


def remove_if_unchanged(shipped: dict) -> list:
	removed = []
	for name, sha256 in shipped.items():
		script = frappe.db.get_value("Client Script", name, "script")
		if script is None:
			continue
		if hashlib.sha256(script.replace("\r\n", "\n").encode()).hexdigest() != sha256:
			continue
		frappe.delete_doc("Client Script", name, ignore_permissions=True, force=True)
		removed.append(name)
	return removed
