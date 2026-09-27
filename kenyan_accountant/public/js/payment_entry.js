// Kenya Tax buttons on Payment Entry (VAT Withholding, WHT Withheld).
// Shipped as app code via doctype_js (it was a Client Script record until
// v16.2.0; patches/v16_2/remove_shipped_client_scripts.py removes that copy).

frappe.ui.form.on("Payment Entry", {
	refresh(frm) {
		if (frm.doc.docstatus !== 0 || !frm.doc.party) {
			return;
		}
		if (frm.doc.party_type === "Supplier") {
			frm.add_custom_button(
				__("Add VAT Withholding"),
				() => kenyan_accountant_add_vat_withholding(frm, "payable"),
				__("Kenya Tax")
			);
		} else if (frm.doc.party_type === "Customer") {
			frm.add_custom_button(
				__("Add VAT Withholding"),
				() => kenyan_accountant_add_vat_withholding(frm, "receivable"),
				__("Kenya Tax")
			);
			frm.add_custom_button(
				__("Add WHT Withheld"),
				() => kenyan_accountant_add_wht_withheld(frm),
				__("Kenya Tax")
			);
		}
	},
});

// Pay (payable, this company withholding from a supplier): real cash out is
// LESS than the invoice, so the deduction amount must be negative and
// paid_amount reduced -- see erpnext's Payment Entry set_difference_amount(),
// which for a Pay entry computes difference = paid_amount - allocated -
// deductions. Receive (receivable, a customer withholding from us): real
// cash in is LESS than the invoice, deduction amount stays positive and
// received_amount is reduced -- for a Receive entry the formula is the
// mirror image (difference = allocated - received + deductions). Confirmed
// against the real formula in erpnext's own source and verified end-to-end
// live, not assumed from either sign looking intuitively right -- the first
// attempt at this got it backwards and only balanced by accident-checking
// difference_amount before every submit.
function kenyan_accountant_apply_deduction(frm, direction, account, amount, description) {
	const row = frm.add_child("deductions");
	row.account = account;
	row.description = description;
	if (direction === "payable") {
		row.amount = -amount;
		frm.set_value("paid_amount", (frm.doc.paid_amount || 0) - amount);
		if (frm.doc.received_amount !== undefined) {
			frm.set_value("received_amount", frm.doc.paid_amount);
		}
	} else {
		row.amount = amount;
		frm.set_value("received_amount", (frm.doc.received_amount || 0) - amount);
		if (frm.doc.paid_amount !== undefined) {
			frm.set_value("paid_amount", frm.doc.received_amount);
		}
	}
	frm.refresh_field("deductions");
	frappe.show_alert({
		message: __("Added deduction of {0} -- review Difference Amount is zero before submitting", [
			format_currency(amount),
		]),
		indicator: "green",
	});
}

function kenyan_accountant_add_vat_withholding(frm, direction) {
	const default_base = frm.doc.paid_amount || frm.doc.received_amount || 0;
	const dialog = new frappe.ui.Dialog({
		title: __("Add VAT Withholding Deduction"),
		fields: [
			{
				fieldname: "taxable_amount",
				fieldtype: "Currency",
				label: __("Taxable (VAT-Exclusive) Value"),
				default: default_base,
				description: __(
					"The VAT-exclusive value of the supply this payment relates to -- not " +
						"necessarily the same as the payment amount if this is a partial payment."
				),
				reqd: 1,
			},
		],
		primary_action_label: __("Add"),
		primary_action(values) {
			frappe.call({
				method: "kenyan_accountant.setup.withholding.compute_vat_withholding_amount",
				args: {
					company: frm.doc.company,
					taxable_amount: values.taxable_amount,
					direction: direction,
				},
				callback(r) {
					if (!r.message) {
						return;
					}
					dialog.hide();
					kenyan_accountant_apply_deduction(
						frm,
						direction,
						r.message.account,
						r.message.amount,
						__("VAT Withholding ({0}%)", [r.message.rate])
					);
				},
			});
		},
	});
	dialog.show();
}

// No computed rate here on purpose -- WHT rate depends on the payment type
// (5% professional/consultancy, 3% contractual, ...), not one flat
// percentage the way VAT Withholding is. Enter the amount straight from the
// withholding certificate the customer provides.
function kenyan_accountant_add_wht_withheld(frm) {
	frappe.call({
		method: "frappe.client.get_value",
		args: {
			doctype: "Kenya Accounting Settings",
			filters: { company: frm.doc.company },
			fieldname: "wht_receivable_account",
		},
		callback(r) {
			const account = r.message && r.message.wht_receivable_account;
			if (!account) {
				frappe.msgprint(__("No WHT Receivable account configured for {0} yet.", [frm.doc.company]));
				return;
			}
			const dialog = new frappe.ui.Dialog({
				title: __("Add WHT Withheld"),
				fields: [
					{
						fieldname: "amount",
						fieldtype: "Currency",
						label: __("Amount Withheld"),
						description: __("From the customer's withholding certificate."),
						reqd: 1,
					},
					{
						fieldname: "certificate_number",
						fieldtype: "Data",
						label: __("Certificate Number"),
					},
				],
				primary_action_label: __("Add"),
				primary_action(values) {
					dialog.hide();
					const description = values.certificate_number
						? __("WHT withheld (certificate {0})", [values.certificate_number])
						: __("WHT withheld");
					kenyan_accountant_apply_deduction(frm, "receivable", account, values.amount, description);
				},
			});
			dialog.show();
		},
	});
}
