// Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on('OTPL Employee Group', {
	refresh(frm) {
		frm.add_custom_button(__('Fetch Employee'), () => {
			open_fetch_employee_dialog(frm);
		});
	}
});

function open_fetch_employee_dialog(frm) {
	let d = new frappe.ui.Dialog({
		title: __('Fetch Employee'),
		fields: [
			{
				fieldname: 'business_vertical',
				label: 'Business Vertical',
				fieldtype: 'Select',
				options: [
					'',
					'RTI',
					'WHEELBURN',
					'TRADA',
					'PAUT USFD Testing',
					'USFD TESTING',
					'INSTRUMENTATION',
					'VEHICULAR USFD',
					'FRACTURE DETECTION',
					'POTAS',
					'RBM',
					'GIRJ',
					'TRANSLAMATIC',
					'ATW',
					'ALL'
				].join('\n')
			},
			{
				fieldname: 'staff_type',
				label: 'Staff Type',
				fieldtype: 'Select',
				options: [
					'',
					'Manager',
					'Worker',
					'Staff',
					'Field',
					'Director',
					'Not Applicable',
					'Consultant'
				].join('\n')
			},
			{
				fieldname: 'location',
				label: 'Location',
				fieldtype: 'Select',
				options: [
					'',
					'Noida',
					'Haridwar',
					'Site',
					'Lucknow'
				].join('\n')
			},
			{
				fieldname: 'is_team_leader',
				label: 'Is Team Leader',
				fieldtype: 'Check'
			}
		],
		primary_action_label: __('Get Employee'),
		primary_action(values) {
			fetch_employees(frm, values, d);
		}
	});

	d.show();
}

function fetch_employees(frm, filters, dialog) {
	frappe.call({
		method: 'employee_self_service.employee_self_service.doctype.otpl_employee_group.otpl_employee_group.fetch_employees',
		args: {
			filters: filters
		},
		freeze: true,
		callback(r) {
			if (!r.message || !r.message.length) {
				frappe.throw(__('No employees found'));
			}

			let existing_employees = new Set(
				(frm.doc.employees || []).map(row => row.employee)
			);

			let added = 0;

			r.message.forEach(emp => {
				if (existing_employees.has(emp.name)) {
					return;
				}

				let row = frm.add_child('employees');
				row.employee = emp.name;
				row.employee_name = emp.employee_name;

				existing_employees.add(emp.name);
				added++;
			});

			frm.refresh_field('employees');
			dialog.hide();

			if (added) {
				frappe.show_alert({
					message: `${added} employees added`,
					indicator: 'green'
				});
			} else {
				frappe.show_alert({
					message: 'All employees already exist',
					indicator: 'orange'
				});
			}
		}
	});
}