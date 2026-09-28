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

// Sign convention, from erpnext's set_difference_amount(): for a Pay entry
// difference = paid - allocated - deductions, so money we keep back from a
// supplier is a NEGATIVE deduction; for a Receive entry difference =
// allocated - received - deductions, so money a customer kept back is a
// POSITIVE deduction. Either way the cash moved is allocated minus what was
// withheld.
async function kenyan_accountant_apply_deduction(frm, direction, account, amount, description) {
	let row = (frm.doc.deductions || []).find((d) => d.account === account);
	if (!row) {
		row = frm.add_child("deductions");
		row.account = account;
	}
	const previous = flt(row.amount);
	row.amount = direction === "payable" ? -amount : amount;
	row.description = description;
	if (!row.cost_center) {
		row.cost_center =
			frm.doc.cost_center ||
			(await frappe.db.get_value("Company", frm.doc.company, "cost_center")).message.cost_center;
	}
	frm.refresh_field("deductions");

	if (!kenyan_accountant_set_amount_from_references(frm, direction)) {
		// No invoices allocated (an advance): take only this change off the amount.
		const change = Math.abs(flt(row.amount)) - Math.abs(previous);
		const field = direction === "payable" ? "paid_amount" : "received_amount";
		kenyan_accountant_set_amounts(frm, flt(frm.doc[field]) - change);
	}

	frappe.show_alert({
		message: __("Withheld {0}. {1} is now {2}.", [
			format_currency(amount, frm.doc.paid_from_account_currency),
			direction === "payable" ? __("Paid Amount") : __("Received Amount"),
			format_currency(
				direction === "payable" ? frm.doc.paid_amount : frm.doc.received_amount,
				frm.doc.paid_from_account_currency
			),
		]),
		indicator: "green",
	});
}

// Cash moved = what the invoices are settled by, less everything withheld.
// Computed from the allocations instead of subtracted from the typed amount,
// so typing the net cash first can't take the tax off twice.
function kenyan_accountant_set_amount_from_references(frm, direction) {
	const allocated = (frm.doc.references || []).reduce((sum, r) => sum + flt(r.allocated_amount), 0);
	if (!allocated) {
		return false;
	}
	const deductions = (frm.doc.deductions || []).reduce((sum, d) => sum + flt(d.amount), 0);
	kenyan_accountant_set_amounts(frm, direction === "payable" ? allocated + deductions : allocated - deductions);
	return true;
}

// Written to the doc directly, not via set_value: erpnext's paid_amount /
// received_amount handlers re-spread the amount across the invoices
// (allocate_amount_to_references), which zeroes the allocations or allocates
// only the reduced cash and leaves the rest of the invoice outstanding.
function kenyan_accountant_set_amounts(frm, amount) {
	if (frm.doc.paid_from_account_currency !== frm.doc.paid_to_account_currency) {
		frappe.throw(__("Kenya Tax deductions support single-currency payments only. Enter this deduction by hand."));
	}
	amount = flt(amount, precision("paid_amount"));
	frm.doc.paid_amount = amount;
	frm.doc.received_amount = amount;
	frm.doc.base_paid_amount = flt(amount * flt(frm.doc.source_exchange_rate || 1), precision("base_paid_amount"));
	frm.doc.base_received_amount = flt(
		amount * flt(frm.doc.target_exchange_rate || 1),
		precision("base_received_amount")
	);
	frm.refresh_fields();
	frm.dirty();
	frm.events.set_unallocated_amount(frm);
}

// Typing the net cash into a payment makes erpnext shrink the invoice
// allocation to that cash, so a full settlement looks like a part payment. When
// a payment allocates less than an invoice's balance, the dialogs ask which it
// is instead of guessing: withheld tax is part of what settles the invoice.
function kenyan_accountant_settles_field(frm) {
	const refs = frm.doc.references || [];
	const short = refs.some((r) => flt(r.allocated_amount) && flt(r.allocated_amount) < flt(r.outstanding_amount));
	if (!short) {
		return null;
	}
	const money = (v) => format_currency(v, frm.doc.paid_from_account_currency);
	const allocated = refs.reduce((s, r) => s + flt(r.allocated_amount), 0);
	const balance = refs.reduce(
		(s, r) => s + (flt(r.allocated_amount) ? Math.max(flt(r.outstanding_amount), flt(r.allocated_amount)) : 0),
		0
	);
	const whole = __("The whole balance of {0} (cash + tax withheld)", [money(balance)]);
	const part = __("Only {0} of it (a part payment)", [money(allocated)]);
	return {
		whole,
		part,
		field: {
			fieldname: "settles",
			fieldtype: "Select",
			label: __("This Payment Settles"),
			options: ["", whole, part].join("\n"),
			reqd: 1,
			description: __(
				"The payment covers less than the invoice balance. If the customer or you withheld tax on the full invoice, choose the whole balance."
			),
		},
	};
}

function kenyan_accountant_settle_whole(frm) {
	for (const r of frm.doc.references || []) {
		if (flt(r.allocated_amount) && flt(r.allocated_amount) < flt(r.outstanding_amount)) {
			r.allocated_amount = flt(r.outstanding_amount);
		}
	}
	frm.refresh_field("references");
	frm.events.set_total_allocated_amount(frm);
}

async function kenyan_accountant_add_vat_withholding(frm, direction) {
	const r = await frappe.call({
		method: "kenyan_accountant.setup.withholding.get_vat_withholding_base",
		args: { company: frm.doc.company, references: frm.doc.references || [] },
	});
	const base = r.message || { base: 0, base_full: 0, rate: 0, invoices: [] };
	const settles = kenyan_accountant_settles_field(frm);

	const dialog = new frappe.ui.Dialog({
		title: __("Add VAT Withholding"),
		fields: [
			...(settles
				? [
						{
							...settles.field,
							onchange() {
								const choice = dialog.get_value("settles");
								dialog.set_value(
									"taxable_amount",
									choice === settles.whole ? base.base_full : choice === settles.part ? base.base : null
								);
							},
						},
				  ]
				: []),
			{
				fieldname: "taxable_amount",
				fieldtype: "Currency",
				label: __("Taxable (VAT-Exclusive) Value"),
				default: settles ? undefined : base.base || undefined,
				description: __(
					"VAT Withholding is {0}% of this. Only VAT-rated lines count: zero-rated and exempt items carry no VAT Withholding.",
					[base.rate]
				),
				reqd: 1,
			},
			{ fieldname: "breakdown", fieldtype: "HTML" },
		],
		primary_action_label: __("Add"),
		primary_action(values) {
			if (settles && values.settles === settles.whole) {
				kenyan_accountant_settle_whole(frm);
			}
			frappe.call({
				method: "kenyan_accountant.setup.withholding.compute_vat_withholding_amount",
				args: {
					company: frm.doc.company,
					taxable_amount: values.taxable_amount,
					direction: direction,
				},
				callback(res) {
					if (!res.message) {
						return;
					}
					dialog.hide();
					kenyan_accountant_apply_deduction(
						frm,
						direction,
						res.message.account,
						res.message.amount,
						__("VAT Withholding ({0}% of {1})", [
							res.message.rate,
							format_currency(values.taxable_amount, frm.doc.paid_from_account_currency),
						])
					);
				},
			});
		},
	});
	dialog.fields_dict.breakdown.$wrapper.html(kenyan_accountant_vat_breakdown(frm, base));
	dialog.show();
}

function kenyan_accountant_vat_breakdown(frm, base) {
	const money = (v) => format_currency(v, frm.doc.paid_from_account_currency);
	if (!base.invoices.length) {
		return `<p class="text-muted small">${__(
			"No invoices are allocated on this payment. Enter the VAT-exclusive value of the VAT-rated supplies it pays for."
		)}</p>`;
	}
	const rows = base.invoices
		.map((inv) => {
			const part = inv.share < 1 ? __(" x {0}% paid now", [flt(inv.share * 100, 2)]) : "";
			let line = `${frappe.utils.escape_html(inv.invoice)}: ${money(inv.taxable_value)}${part} = <b>${money(inv.base)}</b>`;
			if (inv.base_full !== inv.base) {
				line += __(" (whole balance: {0})", [money(inv.base_full)]);
			}
			if (inv.deemed_inclusive_base !== undefined) {
				line += `<br><span class="text-warning">${__(
					"No VAT charged on this invoice. If it is zero-rated or exempt, there is no VAT Withholding. If the supplier is VAT-registered and simply didn't show VAT, KRA treats the amount as VAT-inclusive: taxable value {0}.",
					[money(inv.deemed_inclusive_base)]
				)}</span>`;
			}
			return `<li>${line}</li>`;
		})
		.join("");
	return `<div class="small"><p class="text-muted">${__("Worked out from the invoices on this payment:")}</p><ul>${rows}</ul></div>`;
}

// No computed rate here on purpose -- WHT rate depends on the payment type
// (5% professional/consultancy, 3% contractual, ...), not one flat
// percentage the way VAT Withholding is. The customer says what they withheld,
// and their certificate confirms it.
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
			const settles = kenyan_accountant_settles_field(frm);
			const dialog = new frappe.ui.Dialog({
				title: __("Add WHT Withheld"),
				fields: [
					...(settles ? [settles.field] : []),
					{
						fieldname: "amount",
						fieldtype: "Currency",
						label: __("Amount Withheld"),
						description: __(
							"What the customer withheld: usually 5% (professional, management, consultancy) or 3% (contractual) of the VAT-exclusive value."
						),
						reqd: 1,
					},
					{
						fieldname: "certificate_number",
						fieldtype: "Data",
						label: __("Certificate Number"),
						description: __("If you have it already. You can add it later on the Withholding Tax Credit."),
					},
				],
				primary_action_label: __("Add"),
				primary_action(values) {
					dialog.hide();
					if (settles && values.settles === settles.whole) {
						kenyan_accountant_settle_whole(frm);
					}
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
