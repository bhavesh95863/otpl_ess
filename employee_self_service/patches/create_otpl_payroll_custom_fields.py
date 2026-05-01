# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	"""
	Add custom fields required by OTPL Payroll:
	  - Employee.uan_no                    (UAN No.)
	  - Attendance.false_attendance        (Check)
	  - Attendance.false_attendance_remarks(Small Text, depends on above)
	Also relabel the existing custom field Employee.advance_to_be_deducted
	from "Salary" to "Gross Salary" as per the salary-sheet specification.
	"""

	custom_fields = {
		"Employee": [
			{
				"fieldname": "uan_no",
				"label": "UAN No.",
				"fieldtype": "Data",
				"insert_after": "esi_number",
				"description": "Universal Account Number (UAN) for PF.",
			}
		],
		"Attendance": [
			{
				"fieldname": "false_attendance",
				"label": "False Attendance",
				"fieldtype": "Check",
				"insert_after": "status",
				"description": "Mark if this attendance was incorrectly marked. "
				"Used by OTPL Payroll to deduct days.",
			},
			{
				"fieldname": "false_attendance_remarks",
				"label": "False Attendance Remarks",
				"fieldtype": "Small Text",
				"insert_after": "false_attendance",
				"depends_on": "eval:doc.false_attendance",
			},
		],
	}

	create_custom_fields(custom_fields, update=True)

	# Relabel existing custom field rather than recreating it (fieldname kept
	# for backward compatibility with everything that already reads it).
	cf = frappe.db.get_value(
		"Custom Field",
		{"dt": "Employee", "fieldname": "advance_to_be_deducted"},
		"name",
	)
	if cf:
		frappe.db.set_value("Custom Field", cf, "label", "Gross Salary")

	frappe.clear_cache(doctype="Employee")
	frappe.clear_cache(doctype="Attendance")
	frappe.db.commit()
