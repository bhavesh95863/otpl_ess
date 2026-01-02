// Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on('Leave Pull', {
	refresh: function(frm) {
		// Add Approve button only if document is saved and not already approved
		if (!frm.is_new() && frm.doc.status !== "Approved" && frm.doc.source_erp) {
			frm.add_custom_button(__('Approve Leave'), function() {
				// Prompt for approved dates
				frappe.prompt([
					{
						fieldname: 'approved_from_date',
						label: __('Approved From Date'),
						fieldtype: 'Date',
						reqd: 1,
						default: frm.doc.from_date
					},
					{
						fieldname: 'approved_to_date',
						label: __('Approved To Date'),
						fieldtype: 'Date',
						reqd: 1,
						default: frm.doc.to_date
					}
				],
				function(values) {
					// Call the approve_leave method
					frappe.call({
						method: 'employee_self_service.employee_self_service.doctype.leave_pull.leave_pull.approve_leave',
						args: {
							docname: frm.doc.name,
							approved_from_date: values.approved_from_date,
							approved_to_date: values.approved_to_date
						},
						callback: function(r) {
							if (r.message && r.message.success) {
								frm.reload_doc();
							}
						}
					});
				},
				__('Approve Leave'),
				__('Approve')
				);
			}, __('Actions'));
		}
	}
});
