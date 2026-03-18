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
			options: '\nChecked In\nNo Team Leader Error\nNot Attempted',
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
		}
	]
};
