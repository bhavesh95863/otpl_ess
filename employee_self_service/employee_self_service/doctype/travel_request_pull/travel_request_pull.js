// Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on('Travel Request Pull', {
	refresh: function(frm) {
		// Add Approve button only if document is saved, not already approved, and from source ERP
		if (!frm.is_new() && frm.doc.status !== "Approved" && frm.doc.source_erp) {
			frm.add_custom_button(__('Approve'), function() {
				frappe.confirm(
					__('Are you sure you want to approve this travel request?'),
					function() {
						frappe.call({
							method: 'employee_self_service.employee_self_service.doctype.travel_request_pull.travel_request_pull.approve_travel_request_pull',
							args: {
								docname: frm.doc.name
							},
							callback: function(r) {
								if (r.message && r.message.success) {
									frm.reload_doc();
								}
							}
						});
					}
				);
			}, __('Actions'));
		}

		// Make form read-only after Approved or Rejected
		if (frm.doc.status === 'Approved' || frm.doc.status === 'Rejected') {
			frm.fields.forEach(function(field) {
				if (field.df.fieldname) {
					frm.set_df_property(field.df.fieldname, 'read_only', 1);
				}
			});
			frm.disable_save();
		}
	}
});
