# -*- coding: utf-8 -*-
# Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
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
			"width": 250,
		},
		{
			"label": "Staff Type",
			"fieldname": "staff_type",
			"fieldtype": "Data",
			"width": 120,
		},
		{
			"label": "Location",
			"fieldname": "location",
			"fieldtype": "Data",
			"width": 120,
		},
		{
			"label": "Sales Order",
			"fieldname": "sales_order",
			"fieldtype": "Data",
			"width": 160,
		},
		{
			"label": "Reporting Manager",
			"fieldname": "reporting_manager",
			"fieldtype": "Data",
			"width": 220,
		},
		{
			"label": "Checkin Time",
			"fieldname": "checkin_time",
			"fieldtype": "Datetime",
			"width": 180,
		},
		{
			"label": "No Team Leader Error",
			"fieldname": "no_team_leader_error",
			"fieldtype": "Link",
			"options": "No Team Leader Error",
			"width": 200,
		},
	]


def get_data(filters):
	date = getdate(filters.get("date") or today())
	day_start = str(date)
	day_end = str(add_days(date, 1))

	status_filter = filters.get("status")
	staff_type_filter = filters.get("staff_type")
	location_filter = filters.get("location")

	emp_fields = [
		"name", "employee_name", "staff_type", "location",
		"sales_order", "reports_to", "external_reporting_manager",
		"external_report_to", "external_sales_order", "external_order",
	]

	if status_filter == "On Leave":
		# Only employees marked as On Leave
		emp_filters = {
			"status": "Active",
			"employee_availability": "On Leave",
		}
		all_employees = frappe.get_all("Employee", filters=emp_filters, fields=emp_fields, order_by="employee_name asc")
	elif not status_filter:
		# Blank: all active employees regardless of leave status
		emp_filters = {"status": "Active"}
		all_employees = frappe.get_all("Employee", filters=emp_filters, fields=emp_fields, order_by="employee_name asc")
	else:
		# Checked In / No Team Leader Error / Not Attempted: exclude on-leave employees
		emp_filters = {
			"status": "Active",
			"employee_availability": ["not in", ["On Leave"]],
		}
		employees = frappe.get_all("Employee", filters=emp_filters, fields=emp_fields, order_by="employee_name asc")

		emp_null_filters = {
			"status": "Active",
			"employee_availability": ["is", "not set"],
		}
		employees_null = frappe.get_all("Employee", filters=emp_null_filters, fields=emp_fields, order_by="employee_name asc")

		seen = set()
		all_employees = []
		for emp in employees + employees_null:
			if emp.name not in seen:
				seen.add(emp.name)
				all_employees.append(emp)

	# 2. Get all check-ins for the date
	log_type_filter = filters.get("log_type")
	change_in_location = filters.get("change_in_location")
	change_in_reports_to = filters.get("change_in_reports_to")

	checkin_conditions = "WHERE time >= %(day_start)s AND time < %(day_end)s"
	checkin_params = {"day_start": day_start, "day_end": day_end}

	if log_type_filter:
		checkin_conditions += " AND log_type = %(log_type)s"
		checkin_params["log_type"] = log_type_filter

	if change_in_location:
		checkin_conditions += " AND team_leader_location_changed = 1"

	if change_in_reports_to:
		checkin_conditions += " AND reports_to_change = 1"

	checkins = frappe.db.sql("""
		SELECT employee, MIN(time) as checkin_time
		FROM `tabEmployee Checkin`
		{conditions}
		GROUP BY employee
	""".format(conditions=checkin_conditions), checkin_params, as_dict=True)

	checkin_map = {c.employee: c.checkin_time for c in checkins}

	# 3. Get all No Team Leader Error entries for the date (latest per employee)
	ntle_entries = frappe.db.sql("""
		SELECT t.employee, t.name, t.datetime
		FROM `tabNo Team Leader Error` t
		INNER JOIN (
			SELECT employee, MAX(datetime) as max_dt
			FROM `tabNo Team Leader Error`
			WHERE datetime >= %(day_start)s AND datetime < %(day_end)s
			GROUP BY employee
		) latest ON t.employee = latest.employee AND t.datetime = latest.max_dt
		WHERE t.datetime >= %(day_start)s AND t.datetime < %(day_end)s
	""", {"day_start": day_start, "day_end": day_end}, as_dict=True)

	ntle_map = {n.employee: n.name for n in ntle_entries}

	# 4. Build report rows
	data = []
	for emp in all_employees:
		# Resolve reporting manager name
		reporting_manager_name = ""
		if emp.external_reporting_manager and emp.external_report_to:
			reporting_manager_name = frappe.db.get_value(
				"Employee Pull", emp.external_report_to, "employee_name"
			) or emp.external_report_to
		elif emp.reports_to:
			reporting_manager_name = frappe.db.get_value(
				"Employee", emp.reports_to, "employee_name"
			) or emp.reports_to

		# Resolve sales order
		if emp.external_sales_order and emp.external_order:
			display_sales_order = emp.external_order
		else:
			display_sales_order = emp.sales_order or ""

		checkin_time = checkin_map.get(emp.name)
		ntle_name = ntle_map.get(emp.name)

		# Apply filters
		if status_filter == "Checked In" and not checkin_time:
			continue
		if status_filter == "No Team Leader Error" and (not ntle_name or checkin_time):
			continue
		if status_filter == "Not Attempted" and (checkin_time or ntle_name):
			continue
		if status_filter == "On Leave":
			pass  # already filtered to on-leave employees
		if staff_type_filter and emp.staff_type != staff_type_filter:
			continue
		if location_filter and emp.location != location_filter:
			continue
		if (change_in_location or change_in_reports_to) and not checkin_time:
			continue

		data.append({
			"employee": emp.name,
			"employee_name": emp.employee_name,
			"staff_type": emp.staff_type or "",
			"location": emp.location or "",
			"sales_order": display_sales_order,
			"reporting_manager": reporting_manager_name,
			"checkin_time": checkin_time,
			"no_team_leader_error": ntle_name,
		})

	# Sort by checkin time descending (entries with no checkin time go last)
	from datetime import datetime
	data.sort(key=lambda x: x.get("checkin_time") or datetime.min, reverse=True)

	return data
