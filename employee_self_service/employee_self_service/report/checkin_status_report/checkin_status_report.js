frappe.query_reports['Checkin Status Report'] = {
	filters: [
		{
			fieldname: 'date',
			label: __('Date'),
			fieldtype: 'Date',
			reqd: 1,
			default: frappe.datetime.get_today()
		},
		{
			fieldname: 'status',
			label: __('Status'),
			fieldtype: 'Select',
			options: '\nChecked In\nNo Team Leader Error\nNot Attempted\nOn Leave',
			default: ''
		},
		{
			fieldname: 'staff_type',
			label: __('Staff Type'),
			fieldtype: 'Select',
			options: '\n' + (frappe.get_meta('Employee').fields.find(f => f.fieldname === 'staff_type')?.options || '').split('\n').join('\n'),
			default: ''
		},
		{
			fieldname: 'location',
			label: __('Location'),
			fieldtype: 'Select',
			options: '\n' + (frappe.get_meta('Employee').fields.find(f => f.fieldname === 'location')?.options || '').split('\n').join('\n'),
			default: ''
		},
		{
			fieldname: 'log_type',
			label: __('Log Type'),
			fieldtype: 'Select',
			options: '\nIN\nOUT',
			default: ''
		},
		{
			fieldname: 'change_in_location',
			label: __('Change in Location'),
			fieldtype: 'Check',
			default: 0
		},
		{
			fieldname: 'change_in_reports_to',
			label: __('Change in Reports To'),
			fieldtype: 'Check',
			default: 0
		}
	]
};
