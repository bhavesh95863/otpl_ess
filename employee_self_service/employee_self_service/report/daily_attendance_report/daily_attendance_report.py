# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.utils import getdate, today, add_days


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{
			"label": "Employee",
			"fieldname": "employee",
			"fieldtype": "Link",
			"options": "Employee",
			"width": 120,
		},
		{
			"label": "Employee Name",
			"fieldname": "employee_name",
			"fieldtype": "Data",
			"width": 200,
		},
		{
			"label": "Business Vertical",
			"fieldname": "business_vertical",
			"fieldtype": "Data",
			"width": 160,
		},
		{
			"label": "Location",
			"fieldname": "location",
			"fieldtype": "Data",
			"width": 120,
		},
		{
			"label": "Check-in Time",
			"fieldname": "checkin_time",
			"fieldtype": "Datetime",
			"width": 180,
		},
		{
			"label": "Check-out Time",
			"fieldname": "checkout_time",
			"fieldtype": "Datetime",
			"width": 180,
		},
		{
			"label": "Attendance",
			"fieldname": "attendance_status",
			"fieldtype": "Data",
			"width": 120,
		},
	]


def get_data(filters):
	date = getdate(filters.get("date") or today())
	day_start = str(date)
	day_end = str(add_days(date, 1))

	# Get all active employees
	employees = frappe.get_all(
		"Employee",
		filters={"status": "Active"},
		fields=["name", "employee_name", "business_vertical", "location"],
		order_by="employee_name asc",
	)

	if not employees:
		return []

	employee_ids = [e.name for e in employees]

	# Get earliest check-in (IN) per employee for the date
	checkins = frappe.db.sql("""
		SELECT employee, MIN(time) as checkin_time
		FROM `tabEmployee Checkin`
		WHERE time >= %(day_start)s AND time < %(day_end)s
		AND log_type = 'IN'
		AND employee IN %(employees)s
		GROUP BY employee
	""", {"day_start": day_start, "day_end": day_end, "employees": employee_ids}, as_dict=True)

	checkin_map = {c.employee: c.checkin_time for c in checkins}

	# Get latest check-out (OUT) per employee for the date
	checkouts = frappe.db.sql("""
		SELECT employee, MAX(time) as checkout_time
		FROM `tabEmployee Checkin`
		WHERE time >= %(day_start)s AND time < %(day_end)s
		AND log_type = 'OUT'
		AND employee IN %(employees)s
		GROUP BY employee
	""", {"day_start": day_start, "day_end": day_end, "employees": employee_ids}, as_dict=True)

	checkout_map = {c.employee: c.checkout_time for c in checkouts}

	# Get attendance records for the date
	attendance_records = frappe.get_all(
		"Attendance",
		filters={
			"attendance_date": date,
			"employee": ["in", employee_ids],
			"docstatus": 1,
		},
		fields=["employee", "status"],
	)

	attendance_map = {a.employee: a.status for a in attendance_records}

	data = []
	for emp in employees:
		attendance_status = attendance_map.get(emp.name, "")

		data.append({
			"employee": emp.name,
			"employee_name": emp.employee_name,
			"business_vertical": emp.business_vertical or "",
			"location": emp.location or "",
			"checkin_time": checkin_map.get(emp.name),
			"checkout_time": checkout_map.get(emp.name),
			"attendance_status": attendance_status or "Not Marked",
		})

	return data
