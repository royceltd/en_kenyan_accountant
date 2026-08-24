// Copyright (c) 2026, Royce Technologies LTD and contributors
// For license information, please see license.txt

frappe.ui.form.on("Kenyan Accountant Settings", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		if (frm.doc.setup_status !== "Activated") {
			frm.add_custom_button(__("Run Setup"), () => {
				frappe.call({
					method: "run_setup",
					doc: frm.doc,
					freeze: true,
					freeze_message: __("Creating Kenya accounts, VAT templates and WHT categories..."),
					callback: () => frm.reload_doc(),
				});
			});
		}

		if (frm.doc.setup_status === "Reviewed") {
			frm.add_custom_button(__("Activate"), () => {
				frappe.confirm(
					__("Activate this configuration for {0}? Make sure it has been tested against real transactions first.", [
						frm.doc.company,
					]),
					() => {
						frappe.call({
							method: "activate",
							doc: frm.doc,
							freeze: true,
							callback: () => frm.reload_doc(),
						});
					}
				);
			});
		}
	},
});
