// Copyright (c) 2026, Royce Technologies LTD and contributors
// For license information, please see license.txt

frappe.listview_settings["Petty Cash Request"] = {
	add_fields: ["status"],
	get_indicator(doc) {
		const colours = { Draft: "gray", "Pending Approval": "orange", Approved: "blue", Rejected: "red", Paid: "green", Cancelled: "red" };
		return [__(doc.status), colours[doc.status] || "gray", "status,=," + doc.status];
	},
	onload(list) {
		const company = frappe.defaults.get_user_default("Company");
		if (!company) return;
		frappe.call("kenyan_accountant.petty_cash.summary", { company }).then(({ message: m }) => {
			if (!m.is_approver) return;
			list.page.set_indicator(
				__("Petty cash: {0}", [format_currency(m.balance)]),
				m.balance > 0 ? "green" : "red",
			);
			list.page.add_inner_button(__("Top Up Petty Cash"), () => {
				const d = new frappe.ui.Dialog({
					title: __("Top up petty cash ({0})", [m.account]),
					fields: [
						{
							fieldname: "from_account",
							fieldtype: "Link",
							options: "Account",
							label: __("From (bank or M-Pesa)"),
							reqd: 1,
							get_query: () => ({ filters: { company, is_group: 0, account_type: ["in", ["Bank", "Cash"]] } }),
						},
						{ fieldname: "amount", fieldtype: "Currency", label: __("Amount"), reqd: 1 },
						{ fieldname: "reference", fieldtype: "Data", label: __("Reference (cheque or M-Pesa code)") },
					],
					primary_action_label: __("Top Up"),
					primary_action: (v) =>
						frappe
							.call({ method: "kenyan_accountant.petty_cash.top_up", args: { company, ...v }, freeze: true })
							.then(({ message }) => {
								d.hide();
								frappe.msgprint(
									__("Topped up ({0}). Petty cash now holds {1}.", [
										message.journal_entry,
										format_currency(message.balance),
									]),
								);
								list.page.set_indicator(__("Petty cash: {0}", [format_currency(message.balance)]), "green");
							}),
				});
				d.show();
			});
		});
	},
};
