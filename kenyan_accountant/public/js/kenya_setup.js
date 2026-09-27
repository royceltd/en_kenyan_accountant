// "Finish setting up your documents": see kenyan_accountant/setup_checklist.py.
// The server only sends a checklist while something is still missing, so this
// disappears on its own once everything's filled in.

$(document).on("startup", () => {
	const items = frappe.boot.kenya_setup_checklist;
	if (!items || !items.length) {
		return;
	}
	try {
		if (sessionStorage.getItem("kenya_setup_later")) {
			return;
		}
	} catch (e) {
		// storage blocked: show it, the user can still dismiss it
	}
	setTimeout(() => kenya_setup_show(items), 1200);
});

function kenya_setup_show(items) {
	const dialog = new frappe.ui.Dialog({
		title: __("Finish setting up your documents"),
		fields: [{ fieldtype: "HTML", fieldname: "list" }],
		primary_action_label: __("Remind me later"),
		primary_action() {
			try {
				sessionStorage.setItem("kenya_setup_later", "1");
			} catch (e) {
				// ignore
			}
			dialog.hide();
		},
		secondary_action_label: __("Don't show again"),
		secondary_action() {
			frappe.call("kenyan_accountant.setup_checklist.dismiss");
			dialog.hide();
		},
	});

	const rows = items
		.map(
			(item, i) => `
			<div class="kenya-setup-item" style="display:flex;align-items:flex-start;gap:12px;padding:10px 0;${
				i ? "border-top:1px solid var(--border-color);" : ""
			}">
				<div style="flex:1;">
					<div style="font-weight:600;">${frappe.utils.escape_html(__(item.label))}</div>
					<div class="text-muted small">${frappe.utils.escape_html(__(item.hint))}</div>
				</div>
				<button class="btn btn-default btn-xs" data-index="${i}">${__("Open")}</button>
			</div>`
		)
		.join("");

	dialog.fields_dict.list.$wrapper.html(`
		<p class="text-muted">${__(
			"These appear on the invoices, quotations and payslips you send. It takes a few minutes."
		)}</p>
		${rows}`);

	dialog.fields_dict.list.$wrapper.find("button[data-index]").on("click", function () {
		const item = items[$(this).data("index")];
		try {
			sessionStorage.setItem("kenya_setup_later", "1");
		} catch (e) {
			// ignore
		}
		dialog.hide();
		frappe.set_route(...item.route);
	});

	dialog.show();
}
