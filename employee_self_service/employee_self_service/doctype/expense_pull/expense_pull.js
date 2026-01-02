// Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on('Expense Pull', {
	refresh: function(frm) {
		// Add Approve button only if document is saved and not already approved
		if (!frm.is_new() && !frm.doc.approved_by_manager && frm.doc.source_erp) {
			frm.add_custom_button(__('Approve Expense'), function() {
				// Prompt for approved amount
				frappe.prompt([
					{
						fieldname: 'amount_approved',
						label: __('Amount Approved'),
						fieldtype: 'Currency',
						reqd: 1,
						default: frm.doc.amount
					}
				],
				function(values) {
					// Call the approve_expense method
					frappe.call({
						method: 'employee_self_service.employee_self_service.doctype.expense_pull.expense_pull.approve_expense',
						args: {
							docname: frm.doc.name,
							amount_approved: values.amount_approved
						},
						callback: function(r) {
							if (r.message && r.message.success) {
								frm.reload_doc();
							}
						}
					});
				},
				__('Approve Expense'),
				__('Approve')
				);
			}, __('Actions'));
		}
	}
});
