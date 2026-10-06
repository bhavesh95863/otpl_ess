// Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
// For license information, please see license.txt

const METHOD_PATH =
	'employee_self_service.employee_self_service.doctype.otpl_leave_restructure.otpl_leave_restructure';

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
	'August', 'September', 'October', 'November', 'December'];

frappe.ui.form.on('OTPL Leave Restructure', {
	onload(frm) {
		if (frm.is_new() && !frm.doc.month) {
			// Default to last month — the one usually being corrected.
			const d = frappe.datetime.add_months(frappe.datetime.get_today(), -1);
			frm.set_value('month', MONTHS[parseInt(d.split('-')[1], 10) - 1]);
			frm.set_value('year', parseInt(d.split('-')[0], 10));
		}
	},

	refresh(frm) {
		frm.set_df_property('get_leaves', 'hidden', frm.doc.docstatus !== 0);
		if (frm.doc.docstatus > 0) {
			frm.add_custom_button(__('OTPL Leaves'), () => {
				const leaves = [...new Set((frm.doc.days || []).map((d) => d.otpl_leave))];
				frappe.set_route('List', 'OTPL Leave', { name: ['in', leaves] });
			}, __('View'));
		}
		highlight_changed_rows(frm);
	},

	get_leaves: (frm) => get_leaves(frm),
	employee: (frm) => get_leaves(frm),
	month: (frm) => get_leaves(frm),
	year: (frm) => get_leaves(frm),
});

function get_leaves(frm) {
	if (!(frm.doc.employee && frm.doc.month && frm.doc.year) || frm.doc.docstatus !== 0) return;
	frappe.call({
		method: `${METHOD_PATH}.get_plan`,
		args: { doc: frm.doc },
		freeze: true,
		freeze_message: __('Reading leave days...'),
		callback(r) {
			if (!r.message) return;
			const fields = ['from_date', 'to_date', 'monthly_cl_cap', 'other_cl_in_month', 'cl_available',
				'current_cl_days', 'current_lwp_days', 'new_cl_days', 'new_lwp_days', 'changed_days',
				'payroll_warning'];
			fields.forEach((f) => { frm.doc[f] = r.message[f]; });
			frm.doc.days = [];
			(r.message.days || []).forEach((d) => {
				const row = frm.add_child('days');
				['leave_date', 'day_weight', 'otpl_leave', 'current_leave_type', 'new_leave_type',
					'changed', 'outside_month', 'current_leave_application'].forEach((f) => { row[f] = d[f]; });
			});
			frm.refresh_fields();
			highlight_changed_rows(frm);
			if (!r.message.days || !r.message.days.length) {
				frappe.msgprint(__('No approved OTPL Leave days found for this employee in {0} {1}.',
					[frm.doc.month, frm.doc.year]));
			} else if (!r.message.changed_days) {
				frappe.show_alert({ message: __('These leave days already follow the Casual Leave rule.'), indicator: 'blue' });
			}
		},
	});
}

function highlight_changed_rows(frm) {
	const grid = frm.fields_dict.days && frm.fields_dict.days.grid;
	if (!grid) return;
	(grid.grid_rows || []).forEach((gr) => {
		$(gr.row).css('background-color', gr.doc.changed ? 'var(--yellow-highlight-color, #fffbe6)' : '');
	});
}
