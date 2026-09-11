// Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
// For license information, please see license.txt

const METHOD_PATH =
	'employee_self_service.employee_self_service.doctype.otpl_casual_leave_adjustment.otpl_casual_leave_adjustment';

frappe.ui.form.on('OTPL Casual Leave Adjustment', {
	onload(frm) {
		sync_employee_options(frm);
	},

	refresh(frm) {
		sync_employee_options(frm);

		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__('Get Employees'), () => get_employees(frm)).addClass('btn-primary');
			frm.add_custom_button(__('Refresh Balances'), () => refresh_balances(frm));
		}
		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(__('Leave Ledger Entries'), () => {
				frappe.set_route('List', 'Leave Ledger Entry', { transaction_name: frm.doc.name });
			}, __('View'));
		}
	},

	get_employees: (frm) => get_employees(frm),
	refresh_balances: (frm) => refresh_balances(frm),

	effective_date: (frm) => refresh_balances(frm),
	allocation_to_date: (frm) => refresh_balances(frm),
});

frappe.ui.form.on('OTPL Casual Leave Adjustment Detail', {
	employee(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.employee) return;

		frappe.call({
			method: `${METHOD_PATH}.get_employee_balance`,
			args: {
				employee: row.employee,
				effective_date: frm.doc.effective_date,
				allocation_to_date: frm.doc.allocation_to_date,
			},
			callback(r) {
				if (!r.message) return;
				Object.keys(r.message).forEach((k) => frappe.model.set_value(cdt, cdn, k, r.message[k]));
				// Default to "no change" so a row only moves the ledger once HR edits it.
				frappe.model.set_value(cdt, cdn, 'new_balance', r.message.current_balance);
			},
		});
	},

	new_balance: (frm, cdt, cdn) => recompute(frm, cdt, cdn),
	current_balance: (frm, cdt, cdn) => recompute(frm, cdt, cdn),

	employees_remove: (frm) => set_totals(frm),
});

// The Staff Type / Location filters exist to slice the Employee list, so their
// choices are taken from the Employee doctype itself rather than kept as a second
// copy that silently drifts when HR adds a location. The JSON ships the same
// options as a fallback for list-view filters and server-side use.
function sync_employee_options(frm) {
	frappe.model.with_doctype('Employee', () => {
		['staff_type', 'location'].forEach((fieldname) => {
			const df = frappe.meta.get_docfield('Employee', fieldname);
			if (df && df.fieldtype === 'Select' && df.options) {
				// These are filters, so a blank first entry is required to mean
				// "no filter". Employee's own fields have no blank -- the value is
				// mandatory there -- so prepend one.
				const options = df.options.startsWith('\n') ? df.options : `\n${df.options}`;
				frm.set_df_property(fieldname, 'options', options);
			}
		});
		frm.refresh_fields(['staff_type', 'location']);
	});
}

function recompute(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	const adjustment = flt(row.new_balance) - flt(row.current_balance);
	frappe.model.set_value(cdt, cdn, 'adjustment', flt(adjustment, 2));
	frappe.model.set_value(
		cdt, cdn, 'balance_after', flt(flt(row.new_balance) - flt(row.leave_taken_after), 2)
	);
	set_totals(frm);
}

function set_totals(frm) {
	const rows = frm.doc.employees || [];
	frm.set_value('total_employees', rows.length);
	frm.set_value(
		'total_adjustment',
		flt(rows.reduce((sum, r) => sum + flt(r.adjustment), 0), 2)
	);
}

function get_employees(frm) {
	if (!frm.doc.effective_date || !frm.doc.allocation_to_date) {
		frappe.msgprint(__('Set the Effective Date and Valid Till first.'));
		return;
	}

	frappe.call({
		method: `${METHOD_PATH}.get_employees`,
		args: {
			effective_date: frm.doc.effective_date,
			allocation_to_date: frm.doc.allocation_to_date,
			staff_type: frm.doc.staff_type,
			location: frm.doc.location,
			employee: frm.doc.employee,
		},
		freeze: true,
		freeze_message: __('Fetching Casual Leave balances...'),
		callback(r) {
			const rows = r.message || [];
			frm.clear_table('employees');
			rows.forEach((d) => {
				const row = frm.add_child('employees');
				Object.assign(row, d);
			});
			frm.refresh_field('employees');
			set_totals(frm);
			frappe.show_alert({
				message: __('{0} employee(s) fetched', [rows.length]),
				indicator: 'green',
			});
		},
	});
}

function refresh_balances(frm) {
	if (!(frm.doc.employees || []).length) return;

	// The server recomputes every row from the ledger; the grid just mirrors it.
	frm.save().then(() => {
		frm.refresh_field('employees');
		set_totals(frm);
	});
}
