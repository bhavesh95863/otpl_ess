# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document

class AttendanceCreationFailedLog(Document):
	pass


def log_attendance_creation_failure(employee, date, reason, error_log=None):
	"""Create an Attendance Creation Failed Log entry.

	Args:
		employee: Employee ID
		date: Date of the failed attendance
		reason: Short description of why it failed
		error_log: Full traceback or error details (optional)
	"""
	try:
		employee_name = frappe.db.get_value("Employee", employee, "employee_name") or ""
		log = frappe.get_doc({
			"doctype": "Attendance Creation Failed Log",
			"employee": employee,
			"employee_name": employee_name,
			"date": date,
			"reason": reason,
			"error_log": error_log or ""
		})
		log.insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(
			title="Failed to create Attendance Creation Failed Log",
			message=frappe.get_traceback()
		)
