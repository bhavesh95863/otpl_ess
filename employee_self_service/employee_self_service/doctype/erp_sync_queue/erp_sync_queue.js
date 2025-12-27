// Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on('ERP Sync Queue', {
	refresh: function(frm) {
		if (frm.doc.status === 'Failed' && frm.doc.retry_count < frm.doc.max_retries) {
			frm.add_custom_button(__('Retry Sync'), function() {
				frappe.call({
					method: 'employee_self_service.employee_self_service.utils.erp_sync.retry_sync_queue_item',
					args: {
						queue_name: frm.doc.name
					},
					callback: function(r) {
						if (r.message) {
							frappe.msgprint(__('Sync queued for retry'));
							frm.reload_doc();
						}
					}
				});
			});
		}
	}
});
