// Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on('OTPL TDS', {
	refresh: lock_booked_rows,
	onload_post_render: lock_booked_rows,
	tds_details_add: lock_booked_rows,
});

frappe.ui.form.on('OTPL TDS Detail', {
	form_render: lock_booked_rows,
});

// A month already posted by the payroll is frozen - the server rejects any
// change to it, so don't let the grid offer one.
function lock_booked_rows(frm) {
	const grid = frm.fields_dict.tds_details && frm.fields_dict.tds_details.grid;
	if (!grid || !grid.grid_rows) return;
	grid.grid_rows.forEach((row) => {
		if (!row.doc) return;
		const locked = !!row.doc.journal_entry;
		['month', 'amount'].forEach((f) => row.toggle_editable(f, !locked));
	});
}
