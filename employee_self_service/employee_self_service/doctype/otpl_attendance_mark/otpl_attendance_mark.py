# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from frappe.utils import getdate


class OTPLAttendanceMark(Document):
	def on_submit(self):
		"""On submit, mark attendance for all employees with new_status set"""
		results = []
		for row in self.employees:
			if not row.new_status:
				results.append("{0} ({1}): Skipped - No new status set".format(row.employee, row.employee_name))
				continue

			try:
				if row.current_attendance and row.current_status != row.new_status:
					existing = frappe.get_doc("Attendance", row.current_attendance)
					if existing.docstatus == 1:
						existing.flags.ignore_permissions = True
						existing.flags.ignore_links = True
						existing.cancel()
					frappe.delete_doc("Attendance", row.current_attendance, ignore_permissions=True, force=True)

					_create_attendance(row.employee, self.date, row.new_status)
					results.append("{0} ({1}): {2} -> {3}".format(
						row.employee, row.employee_name, row.current_status, row.new_status
					))

				elif not row.current_attendance:
					_create_attendance(row.employee, self.date, row.new_status)
					results.append("{0} ({1}): Marked {2}".format(
						row.employee, row.employee_name, row.new_status
					))

				else:
					results.append("{0} ({1}): Already {2} - Skipped".format(
						row.employee, row.employee_name, row.current_status
					))

			except Exception as e:
				frappe.log_error(
					title="OTPL Attendance Mark Error: {0}".format(row.employee),
					message=frappe.get_traceback()
				)
				results.append("{0} ({1}): Error - {2}".format(
					row.employee, row.employee_name, str(e)
				))

		self.db_set("processing_log", "\n".join(results))
		frappe.db.commit()


@frappe.whitelist()
def get_field_options():
	"""Fetch distinct staff_type and location values from Employee doctype"""
	staff_types = frappe.db.sql_list(
		"SELECT DISTINCT staff_type FROM `tabEmployee` WHERE status='Active' AND staff_type IS NOT NULL AND staff_type != '' AND staff_type != '.' ORDER BY staff_type"
	)
	locations = frappe.db.sql_list(
		"SELECT DISTINCT location FROM `tabEmployee` WHERE status='Active' AND location IS NOT NULL AND location != '' AND location != '.' ORDER BY location"
	)
	return {
		"staff_types": staff_types,
		"locations": locations
	}


@frappe.whitelist()
def fetch_employees(staff_type=None, location=None, date=None, company=None, employee=None):
	"""Fetch employees based on filters, with their current attendance status"""
	date = getdate(date)

	filters = {
		"status": "Active"
	}

	if employee:
		filters["name"] = employee
	else:
		if staff_type:
			filters["staff_type"] = staff_type
		if location:
			filters["location"] = location

	if company:
		filters["company"] = company

	employees = frappe.get_all(
		"Employee",
		filters=filters,
		fields=["name", "employee_name", "department", "designation"],
		order_by="employee_name asc"
	)

	# Get existing attendance for these employees on the given date
	employee_ids = [e.name for e in employees]
	if not employee_ids:
		return []

	attendance_map = {}
	if employee_ids:
		attendance_records = frappe.get_all(
			"Attendance",
			filters={
				"employee": ["in", employee_ids],
				"attendance_date": date,
				"docstatus": 1
			},
			fields=["employee", "status", "name"]
		)
		for att in attendance_records:
			attendance_map[att.employee] = {
				"status": att.status,
				"name": att.name
			}

	result = []
	for emp in employees:
		att_info = attendance_map.get(emp.name, {})
		result.append({
			"employee": emp.name,
			"employee_name": emp.employee_name,
			"department": emp.department,
			"designation": emp.designation,
			"current_status": att_info.get("status", "Not Marked"),
			"current_attendance": att_info.get("name", ""),
			"new_status": ""
		})

	return result


def _create_attendance(employee, date, status):
	"""Create and submit a new attendance record"""
	attendance = frappe.get_doc({
		"doctype": "Attendance",
		"employee": employee,
		"attendance_date": date,
		"status": status,
		"remarks": "Marked via OTPL Attendance Mark"
	})
	attendance.insert(ignore_permissions=True)
	attendance.submit()
	return attendance
