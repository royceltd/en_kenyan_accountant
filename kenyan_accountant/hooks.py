app_name = "kenyan_accountant"
app_title = "Accountant"
app_publisher = "Royce Technologies LTD"
app_description = "Kenya Chart of Accounts, VAT and WHT configuration for ERPNext"
app_email = "developer@roycetechnologies.co.ke"
app_license = "mit"

# Apps
# ------------------

# Accounts/VAT/WHT setup operates on Company, Account and tax template doctypes,
# which belong to ERPNext, not core Frappe - required so install-app doesn't fail
# at doctype sync. Same rationale royce_etims documents for its own erpnext dependency.
required_apps = ["erpnext"]

# Ships our own branded print layouts as the default for every new tenant --
# installed automatically via bench install-app, no per-tenant manual setup step.
# The Property Setters below are what actually make each one the DEFAULT a
# customer sees without picking it from a dropdown first -- a Print Format
# record alone just makes one *available*, it doesn't select it (Frappe's
# print dialog reads DocType meta.default_print_format, which only a
# Property Setter on the standard doctype can set without forking it).
fixtures = [
	{
		"doctype": "Custom Field",
		"filters": [
			["is_system_generated", "=", 0],
			["module", "=", "Kenyan Accountant"],
		],
	},
	{
		"doctype": "Client Script",
		"filters": [["module", "=", "Kenyan Accountant"]],
	},
	{
		"doctype": "Print Format",
		"filters": [["name", "in", [
			"Royce Kenya Invoice",
			"Royce Kenya Quotation",
			"Royce Kenya Purchase Order",
			"Royce Kenya Purchase Invoice",
			"Royce Kenya Payment Receipt",
		]]],
	},
	{
		"doctype": "Property Setter",
		"filters": [["name", "in", [
			"Sales Invoice-main-default_print_format",
			"Quotation-main-default_print_format",
			"Purchase Order-main-default_print_format",
			"Purchase Invoice-main-default_print_format",
			"Payment Entry-main-default_print_format",
		]]],
	},
]

# See kenyan_accountant.setup.withholding's module docstring for why WHT
# withheld BY this company (Purchase Invoice, ERPNext's own engine) and
# everything else (Payment Entry deductions) are separate mechanisms feeding
# one tracking doctype (Withholding Tax Credit) rather than one combined
# engine -- and why there is deliberately no Sales Invoice hook here.
doc_events = {
	"Payment Entry": {
		"on_submit": "kenyan_accountant.setup.withholding.sync_payment_withholding_credits",
		"on_cancel": "kenyan_accountant.setup.withholding.sync_payment_withholding_credits",
		"on_trash": "kenyan_accountant.setup.withholding.sync_payment_withholding_credits",
	},
	"Purchase Invoice": {
		"on_submit": "kenyan_accountant.setup.withholding.sync_wht_credits",
		"on_cancel": "kenyan_accountant.setup.withholding.sync_wht_credits",
	},
}

# Each item in the list will be shown as an app in the apps page
add_to_apps_screen = [
	{
		"name": "kenyan_accountant",
		"logo": "/assets/kenyan_accountant/logo.svg",
		"title": "Accountant",
		"route": "/app/kenyan-accountant-settings",
		"has_permission": "kenyan_accountant.check_app_permission",
	}
]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/kenyan_accountant/css/kenyan_accountant.css"
# app_include_js = "/assets/kenyan_accountant/js/kenyan_accountant.js"

# include js, css files in header of web template
# web_include_css = "/assets/kenyan_accountant/css/kenyan_accountant.css"
# web_include_js = "/assets/kenyan_accountant/js/kenyan_accountant.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "kenyan_accountant/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "kenyan_accountant/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "kenyan_accountant.utils.jinja_methods",
# 	"filters": "kenyan_accountant.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "kenyan_accountant.install.before_install"
# after_install = "kenyan_accountant.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "kenyan_accountant.uninstall.before_uninstall"
# after_uninstall = "kenyan_accountant.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "kenyan_accountant.utils.before_app_install"
# after_app_install = "kenyan_accountant.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "kenyan_accountant.utils.before_app_uninstall"
# after_app_uninstall = "kenyan_accountant.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "kenyan_accountant.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "kenyan_accountant.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"kenyan_accountant.tasks.all"
# 	],
# 	"daily": [
# 		"kenyan_accountant.tasks.daily"
# 	],
# 	"hourly": [
# 		"kenyan_accountant.tasks.hourly"
# 	],
# 	"weekly": [
# 		"kenyan_accountant.tasks.weekly"
# 	],
# 	"monthly": [
# 		"kenyan_accountant.tasks.monthly"
# 	],
# }

# Testing
# -------

before_tests = "kenyan_accountant.setup.utils.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "kenyan_accountant.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "kenyan_accountant.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "kenyan_accountant.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["kenyan_accountant.utils.before_request"]
# after_request = ["kenyan_accountant.utils.after_request"]

# Job Events
# ----------
# before_job = ["kenyan_accountant.utils.before_job"]
# after_job = ["kenyan_accountant.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"kenyan_accountant.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

