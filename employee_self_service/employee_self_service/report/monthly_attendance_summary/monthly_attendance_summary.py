# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.utils import getdate, add_days
from calendar import monthrange
from employee_self_service.mobile.v1.attendance import (
	get_attendance_records,
	get_employee_holidays,
	build_attendance_data,
)


def execute(filters=None):
	year = int(filters.get("year"))
	month = int(filters.get("month"))
	days_in_month = monthrange(year, month)[1]

	columns = get_columns(year, month, days_in_month)
	data = get_data(filters, year, month, days_in_month)
	chart = get_chart(data)

	return columns, data, None, chart


def get_columns(year, month, days_in_month):
	columns = [
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
			"width": 180,
		},
		{
			"label": "Department",
			"fieldname": "department",
			"fieldtype": "Data",
			"width": 140,
		},
	]

	# Add one column per day of the month
	for day in range(1, days_in_month + 1):
		date = getdate(f"{year}-{month:02d}-{day:02d}")
		day_abbr = date.strftime("%a")  # Mon, Tue, etc.
		columns.append({
			"label": f"{day} {day_abbr}",
			"fieldname": f"day_{day}",
			"fieldtype": "Data",
			"width": 70,
		})

	# Summary columns
	columns.extend([
		{"label": "Present", "fieldname": "total_present", "fieldtype": "Int", "width": 75},
		{"label": "Absent", "fieldname": "total_absent", "fieldtype": "Int", "width": 75},
		{"label": "Leave", "fieldname": "total_leave", "fieldtype": "Int", "width": 75},
		{"label": "Half Day", "fieldname": "total_half_day", "fieldtype": "Int", "width": 80},
		{"label": "Holiday", "fieldname": "total_holiday", "fieldtype": "Int", "width": 75},
		{"label": "No Record", "fieldname": "total_no_record", "fieldtype": "Int", "width": 85},
	])

	return columns


def get_data(filters, year, month, days_in_month):
	month_start = f"{year}-{month:02d}-01"
	month_end = f"{year}-{month:02d}-{days_in_month}"

	# Build employee filters
	emp_filters = {"status": "Active"}
	if filters.get("employee"):
		emp_filters["name"] = filters.get("employee")
	if filters.get("department"):
		emp_filters["department"] = filters.get("department")
	if filters.get("company"):
		emp_filters["company"] = filters.get("company")

	employees = frappe.get_all(
		"Employee",
		filters=emp_filters,
		fields=["name", "employee_name", "department", "company", "no_check_in"],
		order_by="employee_name asc",
	)

	if not employees:
		return []

	today = getdate()
	yesterday = getdate(add_days(today, -1))
	data = []

	for emp in employees:
		row = {
			"employee": emp.name,
			"employee_name": emp.employee_name,
			"department": emp.department or "",
			"total_present": 0,
			"total_absent": 0,
			"total_leave": 0,
			"total_half_day": 0,
			"total_holiday": 0,
			"total_no_record": 0,
		}

		# Use shared build_attendance_data for consistent holiday/no_check_in handling
		attendance_records = get_attendance_records(emp.name, month_start, month_end)
		emp_holidays = get_employee_holidays(emp.name, month_start, month_end)
		emp_data = {"no_check_in": emp.get("no_check_in")}
		attendance_map = build_attendance_data(
			year, month, days_in_month, attendance_records, emp_holidays, emp_data
		)

		# Track original "On Leave" dates (build_attendance_data maps them to "Absent")
		on_leave_dates = {
			getdate(rec.attendance_date).strftime("%Y-%m-%d")
			for rec in attendance_records
			if rec.status == "On Leave"
		}

		for day in range(1, days_in_month + 1):
			date = getdate(f"{year}-{month:02d}-{day:02d}")
			date_str = date.strftime("%Y-%m-%d")

			# Only show data up to yesterday; today and future dates are blank
			if date > yesterday:
				row[f"day_{day}"] = ""
				continue

			status = attendance_map.get(date_str, "")

			# Restore "On Leave" distinction
			if status == "Absent" and date_str in on_leave_dates:
				status = "On Leave"

			if status == "Present":
				display = "P"
				row["total_present"] += 1
			elif status == "Absent":
				display = "A"
				row["total_absent"] += 1
			elif status == "On Leave":
				display = "L"
				row["total_leave"] += 1
			elif status == "Half Day":
				display = "HD"
				row["total_half_day"] += 1
			elif status == "Work From Home":
				display = "WFH"
				row["total_present"] += 1
			elif status == "Holiday":
				display = "H"
				row["total_holiday"] += 1
			elif status == "No Record":
				display = "-"
				row["total_no_record"] += 1
			else:
				display = status[:1] if status else ""
				if status:
					row["total_present"] += 1

			row[f"day_{day}"] = display

		data.append(row)

	return data


def get_chart(data):
	if not data:
		return None

	total_present = sum(d["total_present"] for d in data)
	total_absent = sum(d["total_absent"] for d in data)
	total_leave = sum(d["total_leave"] for d in data)
	total_half_day = sum(d["total_half_day"] for d in data)
	total_holiday = sum(d["total_holiday"] for d in data)
	total_no_record = sum(d["total_no_record"] for d in data)

	return {
		"data": {
			"labels": ["Present", "Absent", "Leave", "Half Day", "Holiday", "No Record"],
			"datasets": [
				{
					"name": "Count",
					"values": [total_present, total_absent, total_leave, total_half_day, total_holiday, total_no_record],
				}
			],
		},
		"type": "bar",
		"colors": ["#36a2eb", "#ff6384", "#ff9f40", "#ffcd56", "#4bc0c0", "#c9cbcf"],
	}
