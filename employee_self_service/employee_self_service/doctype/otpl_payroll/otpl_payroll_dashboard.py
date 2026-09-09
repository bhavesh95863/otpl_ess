from frappe import _


def get_data():
	return {
		"fieldname": "otpl_payroll",
		"non_standard_fieldnames": {"Journal Entry": "otpl_ref_name"},
		"transactions": [
			{"label": _("Accounting"), "items": ["Journal Entry"]},
			{"label": _("Payment"), "items": ["Salary Payable Request"]},
		],
	}
