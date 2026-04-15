frappe.query_reports['Monthly Attendance Summary'] = {
	filters: [
		{
			fieldname: 'year',
			label: __('Year'),
			fieldtype: 'Select',
			options: get_year_options(),
			reqd: 1,
			default: frappe.datetime.str_to_obj(frappe.datetime.get_today()).getFullYear().toString()
		},
		{
			fieldname: 'month',
			label: __('Month'),
			fieldtype: 'Select',
			options: '\n1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n11\n12',
			reqd: 1,
			default: (frappe.datetime.str_to_obj(frappe.datetime.get_today()).getMonth() + 1).toString()
		},
		{
			fieldname: 'employee',
			label: __('Employee'),
			fieldtype: 'Link',
			options: 'Employee'
		},
		{
			fieldname: 'department',
			label: __('Department'),
			fieldtype: 'Link',
			options: 'Department'
		},
		{
			fieldname: 'company',
			label: __('Company'),
			fieldtype: 'Link',
			options: 'Company'
		}
	],

	formatter: function(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (column.fieldname && column.fieldname.startsWith('day_')) {
			var raw = strip_html(value || '');
			if (raw === 'P' || raw === 'WFH') {
				value = '<span style="color:#36a2eb; font-weight:bold;">' + raw + '</span>';
			} else if (raw === 'A') {
				value = '<span style="color:#ff6384; font-weight:bold;">' + raw + '</span>';
			} else if (raw === 'L') {
				value = '<span style="color:#ff9f40; font-weight:bold;">' + raw + '</span>';
			} else if (raw === 'HD') {
				value = '<span style="color:#ffcd56; font-weight:bold;">' + raw + '</span>';
			} else if (raw === 'H') {
				value = '<span style="color:#4bc0c0; font-weight:bold;">' + raw + '</span>';
			} else if (raw === '-') {
				value = '<span style="color:#c9cbcf;">' + raw + '</span>';
			}
		}

		return value;
	}
};

function strip_html(html) {
	var tmp = document.createElement('DIV');
	tmp.innerHTML = html;
	return tmp.textContent || tmp.innerText || '';
}

function get_year_options() {
	var current_year = frappe.datetime.str_to_obj(frappe.datetime.get_today()).getFullYear();
	var options = '';
	for (var y = current_year - 2; y <= current_year + 1; y++) {
		options += '\n' + y;
	}
	return options;
}
