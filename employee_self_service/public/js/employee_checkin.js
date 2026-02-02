frappe.ui.form.on('Employee Checkin', {
	setup(frm) {
		// Override Employee link formatter for Employee Checkin doctype
		const original_formatter = frappe.form.link_formatters['Employee'];
		
		frappe.form.link_formatters['Employee'] = function(value, doc) {
			// If we're in Employee Checkin and dealing with requested_from field, use requested_from_name
			if (doc && doc.doctype === 'Employee Checkin' && doc.requested_from === value && doc.requested_from_name) {
				return value ? value + ': ' + doc.requested_from_name : doc.requested_from_name;
			}
			// Otherwise use the original formatter
			else if (original_formatter) {
				return original_formatter(value, doc);
			}
			// Fallback to default
			else if (doc && doc.employee_name && doc.employee_name !== value) {
				return value ? value + ': ' + doc.employee_name : doc.employee_name;
			} else {
				return value;
			}
		};
	},
	
	refresh(frm) {
		// Make form completely read-only for all fields
		frm.fields.forEach(function(field) {
			frm.set_df_property(field.df.fieldname, 'read_only', 1);
		});
		frm.disable_save();
		
		// Add View Location button
		frm.add_custom_button("View Location", function(){
			window.open("https://www.google.com/maps/search/?api=1&query=" + frm.doc.location, '_blank');
		});
		
		// Add Approve button if conditions are met
		if (frm.doc.approval_required && !frm.doc.approved) {
			// Check if user is Administrator or manager
			if (frappe.user.has_role('Administrator') || frappe.session.user === frm.doc.manager) {
				frm.add_custom_button(__('Approve'), function() {
					// Show dialog to change log time
					let d = new frappe.ui.Dialog({
						title: __('Approve Check-in'),
						fields: [
							{
								label: __('Log Time'),
								fieldname: 'log_time',
								fieldtype: 'Datetime',
								default: frm.doc.time,
								reqd: 1
							}
						],
						primary_action_label: __('Approve'),
						primary_action(values) {
							frappe.call({
								method: 'employee_self_service.employee_self_service.utils.otpl_attendance.approve_checkin',
								args: {
									checkin_name: frm.doc.name,
									log_time: values.log_time
								},
								callback: function(r) {
									if (!r.exc) {
										frappe.show_alert({
											message: __('Check-in approved successfully'),
											indicator: 'green'
										});
										d.hide();
										frm.reload_doc();
									}
								}
							});
						}
					});
					d.show();
				}).addClass('btn-primary');
			}
		}
	}
})