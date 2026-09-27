# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

import frappe

from kenyan_accountant.printing import set_default_print_formats


def after_install():
	"""Runs once, when the app is installed on a site (never on migrate). The
	standard print formats are already synced by this point (install_app syncs
	the module before calling after_install)."""
	done = set_default_print_formats()
	frappe.logger("kenyan_accountant").info(f"after_install: default print formats set for {done}")
