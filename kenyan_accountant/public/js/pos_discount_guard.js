// Hide the till's "Add Discount" when the till (POS Profile) does not allow discounts.
// ERPNext creates the button visible and only ever un-hides it, so "Allow User to Edit
// Discount = off" does not hide it. Managers keep it (the server rule in pos_controls.py
// allows them); everyone else is also blocked on the server.
(function () {
	const MANAGER_ROLES = ["Sales Manager", "Accounts Manager", "System Manager"];

	function hide_discount() {
		const pos = window.cur_pos;
		if (!pos || !pos.settings || pos.settings.allow_discount_change === undefined) return;
		if (pos.settings.allow_discount_change || frappe.user.has_role(MANAGER_ROLES)) return;
		document.querySelectorAll(".point-of-sale-app .add-discount-wrapper").forEach((el) => {
			if (!el.classList.contains("d-none")) el.classList.add("d-none");
		});
	}

	new MutationObserver(hide_discount).observe(document.body, { childList: true, subtree: true });
})();
