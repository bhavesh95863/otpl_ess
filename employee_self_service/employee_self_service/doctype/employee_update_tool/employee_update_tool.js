// Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on('Employee Update Tool', {
	refresh: function(frm) {
		frm.trigger('toggle_tables');

		frm.set_query('employee', 'employee_update_team_leader', function() {
			return {
				filters: {
					is_team_leader: 1
				}
			};
		});
	},

	update_for: function(frm) {
		frm.trigger('toggle_tables');

		// Clear the opposite table and filters when selection changes
		if (frm.doc.update_for === 'Team Leader') {
			frm.doc.employee_update_non_team_leader = [];
			frm.refresh_field('employee_update_non_team_leader');
			frm.set_value('staff_type', '');
			frm.set_value('location', '');
			frm.set_value('business_vertical', '');
		} else if (frm.doc.update_for === 'Non Team Leader') {
			frm.doc.employee_update_team_leader = [];
			frm.refresh_field('employee_update_team_leader');
		}
	},

	get_employees: function(frm) {
		if (!frm.doc.update_for) {
			frappe.msgprint(__('Please select Update For first.'));
			return;
		}

		frappe.call({
			method: 'employee_self_service.employee_self_service.doctype.employee_update_tool.employee_update_tool.get_employees',
			args: {
				update_for: frm.doc.update_for,
				staff_type: frm.doc.staff_type || '',
				location: frm.doc.location || '',
				business_vertical: frm.doc.business_vertical || ''
			},
			freeze: true,
			freeze_message: __('Fetching Employees...'),
			callback: function(r) {
				if (r.message && r.message.length) {
					if (frm.doc.update_for === 'Team Leader') {
						frm.doc.employee_update_team_leader = [];
						$.each(r.message, function(i, d) {
							var row = frm.add_child('employee_update_team_leader');
							row.employee = d.employee;
							row.employee_name = d.employee_name;
							row.sales_order = d.sales_order;
						});
						frm.refresh_field('employee_update_team_leader');
					} else {
						frm.doc.employee_update_non_team_leader = [];
						$.each(r.message, function(i, d) {
							var row = frm.add_child('employee_update_non_team_leader');
							row.employee = d.employee;
							row.employee_name = d.employee_name;
							row.report_to = d.report_to;
							row.external_report_to = d.external_report_to;
						});
						frm.refresh_field('employee_update_non_team_leader');
					}
					frappe.msgprint(__('Fetched {0} employee(s).', [r.message.length]));
				} else {
					frappe.msgprint(__('No employees found for the given filters.'));
				}
			}
		});
	},

	toggle_tables: function(frm) {
		frm.toggle_display('employee_update_team_leader',
			frm.doc.update_for === 'Team Leader');
		frm.toggle_display('section_break_2',
			frm.doc.update_for === 'Team Leader');

		frm.toggle_display('employee_update_non_team_leader',
			frm.doc.update_for === 'Non Team Leader');
		frm.toggle_display('section_break_4',
			frm.doc.update_for === 'Non Team Leader');
	}
});
