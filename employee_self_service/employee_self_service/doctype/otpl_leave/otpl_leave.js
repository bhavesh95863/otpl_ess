// Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on('OTPL Leave', {
	refresh: function(frm) {
		// Always make status field readonly
		frm.set_df_property('status', 'read_only', 1);
		
		// Add custom buttons based on current status
		if (!frm.is_new() && frm.doc.docstatus === 0) {
			// Remove all existing custom buttons first
			frm.clear_custom_buttons();
			
			if (frm.doc.status === 'Pending') {
				// Show Approve and Reject buttons for Pending status
				frm.add_custom_button(__('Approve'), function() {
					frm.trigger('approve_leave');
				}, __('Actions')).addClass('btn-primary');
				
				frm.add_custom_button(__('Reject'), function() {
					frm.trigger('reject_leave');
				}, __('Actions'));
			}
			
			if (frm.doc.status === 'Approved') {
				// Show Cancel button for Approved status
				frm.add_custom_button(__('Cancel Leave'), function() {
					frm.trigger('cancel_leave');
				}).addClass('btn-danger');
			}
		}
		
		// Make form readonly if status is Approved or Cancelled
		if ((frm.doc.status === 'Approved' || frm.doc.status === 'Cancelled') && !frm.is_new()) {
			frm.set_df_property('from_date', 'read_only', 1);
			frm.set_df_property('to_date', 'read_only', 1);
			frm.set_df_property('approved_from_date', 'read_only', 1);
			frm.set_df_property('approved_to_date', 'read_only', 1);
			frm.set_df_property('employee', 'read_only', 1);
			frm.set_df_property('half_day', 'read_only', 1);
			frm.set_df_property('half_day_date', 'read_only', 1);
			frm.set_df_property('total_no_of_days', 'read_only', 1);
			frm.set_df_property('total_no_of_approved_days', 'read_only', 1);
			frm.set_df_property('alternate_mobile_no', 'read_only', 1);
			frm.set_df_property('reason', 'read_only', 1);
			frm.set_df_property('approver', 'read_only', 1);
			frm.set_df_property('is_external_manager', 'read_only', 1);
			frm.set_df_property('external_manager', 'read_only', 1);
			
			// Show appropriate message
			if (frm.doc.status === 'Approved') {
				frm.dashboard.add_comment(__('This leave has been approved. Use the "Cancel Leave" button to cancel.'), 'green', true);
			} else if (frm.doc.status === 'Cancelled') {
				frm.dashboard.add_comment(__('This leave has been cancelled. All linked Leave Applications have been cancelled.'), 'orange', true);
				frm.disable_save();
			}
		}
		
		// Show message for Rejected status
		if (frm.doc.status === 'Rejected' && !frm.is_new()) {
			frm.dashboard.add_comment(__('This leave has been rejected.'), 'red', true);
		}
		
		// Show linked Leave Applications if any
		if (frm.doc.leave_applications && !frm.is_new()) {
			let leave_apps = frm.doc.leave_applications.split(',').map(s => s.trim());
			if (leave_apps.length > 0) {
				let status_text = frm.doc.status === 'Cancelled' ? '(Cancelled)' : '';
				let html = `<div class="form-message blue"><strong>Linked Leave Applications ${status_text}:</strong><ul>`;
				leave_apps.forEach(function(app) {
					html += `<li><a href="/desk#Form/Leave Application/${app}" target="_blank">${app}</a></li>`;
				});
				html += '</ul></div>';
				frm.dashboard.set_headline_alert(html);
			}
		}
	},
	
	approve_leave: function(frm) {
		frappe.prompt([
			{
				'fieldname': 'approved_from_date',
				'fieldtype': 'Date',
				'label': __('Approved From Date'),
				'reqd': 1,
				'default': frm.doc.from_date
			},
			{
				'fieldname': 'approved_to_date',
				'fieldtype': 'Date',
				'label': __('Approved To Date'),
				'reqd': 1,
				'default': frm.doc.to_date
			}
		],
		function(values) {
			// Calculate approved days
			let approved_days = frappe.datetime.get_day_diff(values.approved_to_date, values.approved_from_date) + 1;
			
			frappe.call({
				method: 'frappe.client.set_value',
				args: {
					doctype: 'OTPL Leave',
					name: frm.doc.name,
					fieldname: {
						'status': 'Approved',
						'approved_from_date': values.approved_from_date,
						'approved_to_date': values.approved_to_date,
						'total_no_of_approved_days': approved_days
					}
				},
				callback: function(r) {
					if (!r.exc) {
						frappe.show_alert({
							message: __('Leave Approved Successfully'),
							indicator: 'green'
						}, 5);
						frm.reload_doc();
					}
				}
			});
		},
		__('Approve Leave'),
		__('Approve')
		);
	},
	
	reject_leave: function(frm) {
		frappe.confirm(
			__('Are you sure you want to reject this OTPL Leave?'),
			function() {
				frappe.call({
					method: 'frappe.client.set_value',
					args: {
						doctype: 'OTPL Leave',
						name: frm.doc.name,
						fieldname: 'status',
						value: 'Rejected'
					},
					callback: function(r) {
						if (!r.exc) {
							frappe.show_alert({
								message: __('Leave Rejected'),
								indicator: 'red'
							}, 5);
							frm.reload_doc();
						}
					}
				});
			}
		);
	},
	
	cancel_leave: function(frm) {
		frappe.confirm(
			__('Are you sure you want to cancel this OTPL Leave? All linked Leave Applications will be cancelled.'),
			function() {
				frappe.call({
					method: 'frappe.client.set_value',
					args: {
						doctype: 'OTPL Leave',
						name: frm.doc.name,
						fieldname: 'status',
						value: 'Cancelled'
					},
					callback: function(r) {
						if (!r.exc) {
							frappe.show_alert({
								message: __('Leave Cancelled Successfully'),
								indicator: 'orange'
							}, 5);
							frm.reload_doc();
						}
					}
				});
			}
		);
	}
});
