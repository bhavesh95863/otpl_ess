frappe.listview_settings['OTPL Leave'] = {
	add_fields: ['status'],

	get_indicator: function(doc) {
		if (doc.status === 'Pending') {
			return [__('Pending'), 'orange', 'status,=,Pending'];
		} else if (doc.status === 'Approved') {
			return [__('Approved'), 'green', 'status,=,Approved'];
		} else if (doc.status === 'Rejected') {
			return [__('Rejected'), 'red', 'status,=,Rejected'];
		} else if (doc.status === 'Cancelled') {
			return [__('Cancelled'), 'grey', 'status,=,Cancelled'];
		}
	},

	onload: function(listview) {
		listview.page.add_action_item(__('Cancel'), function() {
			const selected = listview.get_checked_items();
			if (!selected.length) {
				frappe.msgprint(__('Please select OTPL Leaves to cancel.'));
				return;
			}

			const non_approved = selected.filter(d => d.status !== 'Approved');
			if (non_approved.length) {
				frappe.msgprint(__('Only Approved OTPL Leaves can be cancelled. Please deselect non-approved items.'));
				return;
			}

			const names = selected.map(d => d.name);
			frappe.confirm(
				__('Are you sure you want to cancel {0} OTPL Leave(s)? All linked Leave Applications will also be cancelled.', [names.length]),
				function() {
					frappe.call({
						method: 'employee_self_service.employee_self_service.doctype.otpl_leave.otpl_leave.bulk_cancel_otpl_leaves',
						args: { names: names },
						freeze: true,
						freeze_message: __('Cancelling OTPL Leaves...'),
						callback: function(r) {
							if (!r.exc) {
								frappe.show_alert({
									message: __('Successfully cancelled {0} OTPL Leave(s)', [names.length]),
									indicator: 'green'
								}, 5);
								listview.refresh();
							}
						}
					});
				}
			);
		});
	}
};
