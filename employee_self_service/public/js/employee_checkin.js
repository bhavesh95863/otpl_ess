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
		// frm.fields.forEach(function(field) {
		// 	frm.set_df_property(field.df.fieldname, 'read_only', 1);
		// });
		// frm.disable_save();
		
		// Add View Location button
		frm.add_custom_button("View Location", function(){
			window.open("https://www.google.com/maps/search/?api=1&query=" + frm.doc.location, '_blank');
		});

		// Add Location History button when team_leader_location_changed is checked
		if (frm.doc.team_leader_location_changed) {
			frm.add_custom_button(__('Location History'), function() {
				frappe.call({
					method: 'employee_self_service.employee_self_service.utils.otpl_attendance.get_location_history',
					args: { employee: frm.doc.employee },
					callback: function(r) {
						if (r.message && r.message.length) {
							let rows = r.message.map(function(d) {
								let loc_link = d.location
									? '<a href="https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(d.location) + '" target="_blank">View Map</a>'
									: '';
								let changed = d.team_leader_location_changed ? 'Yes' : 'No';
								let dist = d.distance_different ? d.distance_different + ' km' : '-';
								return '<tr>'
									+ '<td>' + frappe.datetime.str_to_user(d.time) + '</td>'
									+ '<td>' + (d.address || d.location || '-') + '</td>'
									+ '<td>' + loc_link + '</td>'
									+ '<td>' + changed + '</td>'
									+ '<td>' + dist + '</td>'
									+ '</tr>';
							}).join('');

							let html = '<table class="table table-bordered table-striped">'
								+ '<thead><tr>'
								+ '<th>' + __('Time') + '</th>'
								+ '<th>' + __('Address / Location') + '</th>'
								+ '<th>' + __('Map') + '</th>'
								+ '<th>' + __('Location Changed') + '</th>'
								+ '<th>' + __('Distance') + '</th>'
								+ '</tr></thead>'
								+ '<tbody>' + rows + '</tbody></table>';

							let d = new frappe.ui.Dialog({
								title: __('Location History (Last 5 Days - IN)'),
								size: 'extra-large'
							});
							d.$body.html(html);
							d.show();
						} else {
							frappe.msgprint(__('No location history found for the last 5 days.'));
						}
					}
				});
			});
		}
		
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