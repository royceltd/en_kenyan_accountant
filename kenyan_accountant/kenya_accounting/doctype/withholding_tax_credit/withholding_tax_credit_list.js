// Copyright (c) 2026, Royce Technologies LTD and contributors
// For license information, please see license.txt

frappe.listview_settings["Withholding Tax Credit"] = {
	onload(listview) {
		// Tick the WHT credits a customer withheld, then draft the Journal Entry
		// that sets them against income tax (setup.withholding.make_wht_claim_journal_entry).
		listview.page.add_actions_menu_item(__("Claim WHT Credits"), () => {
			const names = listview.get_checked_items(true);
			if (!names.length) {
				frappe.msgprint(__("Tick the WHT credits you are claiming on this return first."));
				return;
			}
			frappe.call({
				method: "kenyan_accountant.setup.withholding.make_wht_claim_journal_entry",
				args: { credits: names },
				freeze: true,
				callback(r) {
					if (r.message) {
						frappe.show_alert({
							message: __("Draft {0} created. Check it and submit to mark the credits claimed.", [r.message]),
							indicator: "green",
						});
						frappe.set_route("Form", "Journal Entry", r.message);
					}
				},
			});
		});
	},
};
