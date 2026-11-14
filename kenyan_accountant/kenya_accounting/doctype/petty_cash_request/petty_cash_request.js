// Copyright (c) 2026, Royce Technologies LTD and contributors
// For license information, please see license.txt

const PC = "kenyan_accountant.petty_cash.";

frappe.ui.form.on("Petty Cash Request", {
	setup(frm) {
		frm.set_query("expense_account", "items", () => ({
			filters: { company: frm.doc.company, is_group: 0, root_type: "Expense" },
		}));
		frm.set_query("cost_center", "items", () => ({ filters: { company: frm.doc.company, is_group: 0 } }));
		frm.set_query("cost_center", () => ({ filters: { company: frm.doc.company, is_group: 0 } }));
		frm.set_query("item", "items", () => ({ filters: { is_stock_item: 0, disabled: 0 } }));
	},

	refresh(frm) {
		const colours = { Draft: "gray", "Pending Approval": "orange", Approved: "blue", Rejected: "red", Paid: "green", Cancelled: "red" };
		if (frm.doc.docstatus === 1) frm.page.set_indicator(__(frm.doc.status), colours[frm.doc.status]);
		if (frm.doc.docstatus !== 1 || !frm.doc.company) return;
		frappe.call(PC + "summary", { company: frm.doc.company }).then(({ message: m }) => {
			if (!m.is_approver) return;
			if (m.account) {
				frm.dashboard.set_headline(
					__("Petty cash ({0}) holds {1}", [m.account, format_currency(m.balance, frm.doc.currency)]),
				);
			}
			const act = (method, args, msg) =>
				frappe.call({ method: PC + method, args: { name: frm.doc.name, ...args }, freeze: true }).then(() => {
					frappe.show_alert({ message: msg, indicator: "green" });
					frm.reload_doc();
				});
			if (frm.doc.status === "Pending Approval") {
				frm.add_custom_button(__("Approve"), () =>
					frappe.prompt(
						{ fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks (optional)") },
						(v) => act("approve", { remarks: v.remarks }, __("Approved")),
						__("Approve request"),
					),
				).addClass("btn-primary");
			}
			if (["Pending Approval", "Approved"].includes(frm.doc.status)) {
				frm.add_custom_button(__("Reject"), () =>
					frappe.prompt(
						{ fieldname: "remarks", fieldtype: "Small Text", label: __("Why?"), reqd: 1 },
						(v) => act("reject", { remarks: v.remarks }, __("Rejected")),
						__("Reject request"),
					),
				);
			}
			if (frm.doc.status === "Approved") {
				frm.add_custom_button(__("Pay"), () =>
					frappe.prompt(
						[
							{
								fieldname: "received_by",
								fieldtype: "Data",
								label: __("Cash received by"),
								default: frm.doc.requested_for || frm.doc.requested_by_name,
								reqd: 1,
							},
							{ fieldname: "posting_date", fieldtype: "Date", label: __("Date"), default: frappe.datetime.get_today() },
						],
						(v) => act("pay", v, __("Paid")),
						__("Pay {0} from petty cash", [format_currency(frm.doc.total_amount, frm.doc.currency)]),
						__("Pay"),
					),
				).addClass("btn-primary");
			}
		});
	},

	item(frm) {
		frm.refresh_field("items");
	},
});

frappe.ui.form.on("Petty Cash Request Item", {
	qty: (frm, cdt, cdn) => set_amount(frm, cdt, cdn),
	rate: (frm, cdt, cdn) => set_amount(frm, cdt, cdn),
	item(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.item && !row.description) {
			frappe.db.get_value("Item", row.item, "item_name").then(({ message }) =>
				frappe.model.set_value(cdt, cdn, "description", message.item_name),
			);
		}
	},
});

function set_amount(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	frappe.model.set_value(cdt, cdn, "amount", flt(row.qty) * flt(row.rate));
	frm.set_value(
		"total_amount",
		(frm.doc.items || []).reduce((t, r) => t + flt(r.amount), 0),
	);
}
