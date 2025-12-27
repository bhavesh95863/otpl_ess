// Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on('ERP Sync Settings', {
	refresh: function(frm) {
		if (frm.doc.enabled && !frm.is_new()) {
			frm.add_custom_button(__('Pull Data from Remote ERP'), function() {
				frappe.confirm(
					__('This will pull all Employee and Sales Order data from the remote ERP. Continue?'),
					function() {
						frappe.call({
							method: 'employee_self_service.employee_self_service.utils.erp_sync.initial_pull_from_remote_erp',
							args: {
								erp_sync_settings: frm.doc.name
							},
							freeze: true,
							freeze_message: __('Pulling data from remote ERP...'),
							callback: function(r) {
								if (r.message && r.message.success) {
									var data = r.message.data;
									var msg = __('Pull completed successfully!') + '<br>';
									msg += __('Employees Pulled: {0}', [data.employees_pulled]) + '<br>';
									msg += __('Sales Orders Pulled: {0}', [data.sales_orders_pulled]);
									
									if (data.errors && data.errors.length > 0) {
										msg += '<br><br>' + __('Errors:') + '<br>' + data.errors.join('<br>');
									}
									
									frappe.msgprint({
										title: __('Pull Complete'),
										message: msg,
										indicator: 'green'
									});
									frm.reload_doc();
								} else {
									frappe.msgprint({
										title: __('Pull Failed'),
										message: r.message ? r.message.message : __('Unknown error occurred'),
										indicator: 'red'
									});
								}
							}
						});
					}
				);
			});
		}
	}
});
