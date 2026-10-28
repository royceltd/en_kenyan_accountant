# Copyright (c) 2026, Royce Technologies LTD and contributors
# For license information, please see license.txt

from kenyan_accountant.printing import set_default_pos_print_format


def execute():
	# Kenya POS Receipt for POS Profiles that have no print format of their own.
	set_default_pos_print_format()
