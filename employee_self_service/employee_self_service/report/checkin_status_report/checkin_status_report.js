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
			options: '\nManager\nWorker\nStaff\nField\nDirector\nNot Applicable\nConsultant\nDriver',
			default: ''
		},
		{
			fieldname: 'location',
			label: __('Location'),
			fieldtype: 'Link',
			options: 'ESS Location',
			default: ''
		}
	]
};
