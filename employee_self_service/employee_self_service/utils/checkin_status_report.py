# -*- coding: utf-8 -*-
# Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.utils import getdate, today, add_days, get_datetime


@frappe.whitelist()
def get_checkin_status_report(date=None):
	"""
	Generate a report of all active employees (not on leave) with their check-in status for a given date.

	Categories:
	1. Checked In - employee has a successful Employee Checkin (IN) record
	2. No Team Leader Error - employee attempted check-in but got blocked (No Team Leader Error entry)
	3. Not Attempted - employee has neither check-in nor error record

	Returns dict with summary counts and detailed employee list.
	"""
	if not date:
		date = today()
	date = getdate(date)

	day_start = str(date)
	day_end = str(add_days(date, 1))

	# 1. Get all active employees not on leave
	employees = frappe.get_all("Employee",
		filters={
			"status": "Active",
			"employee_availability": ["not in", ["On Leave"]],
		},
		fields=[
			"name", "employee_name", "staff_type", "location",
			"sales_order", "reports_to", "external_reporting_manager",
			"external_report_to", "external_sales_order", "external_order",
			"is_team_leader"
		],
		order_by="employee_name asc"
	)

	# Also include employees where employee_availability is null/empty
	employees_null = frappe.get_all("Employee",
		filters={
			"status": "Active",
			"employee_availability": ["is", "not set"],
		},
		fields=[
			"name", "employee_name", "staff_type", "location",
			"sales_order", "reports_to", "external_reporting_manager",
			"external_report_to", "external_sales_order", "external_order",
			"is_team_leader"
		],
		order_by="employee_name asc"
	)

	# Merge and deduplicate
	seen = set()
	all_employees = []
	for emp in employees + employees_null:
		if emp.name not in seen:
			seen.add(emp.name)
			all_employees.append(emp)

	# 2. Get all check-ins for the date (IN logs)
	checkins = frappe.db.sql("""
		SELECT employee, MIN(time) as checkin_time
		FROM `tabEmployee Checkin`
		WHERE time >= %(day_start)s AND time < %(day_end)s
		AND log_type = 'IN'
		GROUP BY employee
	""", {"day_start": day_start, "day_end": day_end}, as_dict=True)

	checkin_map = {c.employee: c.checkin_time for c in checkins}

	# 3. Get all No Team Leader Error entries for the date
	ntle_entries = frappe.db.sql("""
		SELECT employee, MIN(datetime) as error_time
		FROM `tabNo Team Leader Error`
		WHERE datetime >= %(day_start)s AND datetime < %(day_end)s
		GROUP BY employee
	""", {"day_start": day_start, "day_end": day_end}, as_dict=True)

	ntle_map = {n.employee: n.error_time for n in ntle_entries}

	# 4. Build report
	checked_in = []
	no_team_leader_error = []
	not_attempted = []

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

		row = {
			"employee": emp.name,
			"employee_name": emp.employee_name,
			"staff_type": emp.staff_type or "",
			"location": emp.location or "",
			"sales_order": display_sales_order,
			"reporting_manager": reporting_manager_name,
		}

		if emp.name in checkin_map:
			row["checkin_time"] = str(checkin_map[emp.name])
			row["status"] = "Checked In"
			checked_in.append(row)
		elif emp.name in ntle_map:
			row["error_time"] = str(ntle_map[emp.name])
			row["status"] = "No Team Leader Error"
			no_team_leader_error.append(row)
		else:
			row["status"] = "Not Attempted"
			not_attempted.append(row)

	total = len(all_employees)

	# Print report
	print("=" * 120)
	print(f"DAILY CHECK-IN STATUS REPORT — {date}")
	print("=" * 120)
	print(f"Total Active Employees (not on leave): {total}")
	print(f"  Checked In:            {len(checked_in)}")
	print(f"  No Team Leader Error:  {len(no_team_leader_error)}")
	print(f"  Not Attempted:         {len(not_attempted)}")
	print("=" * 120)

	# Section 1: Checked In
	print(f"\n{'─' * 120}")
	print(f"  CHECKED IN ({len(checked_in)})")
	print(f"{'─' * 120}")
	if checked_in:
		print(f"{'Employee':<14} {'Name':<35} {'Staff Type':<12} {'Location':<12} {'Sales Order':<16} {'Manager':<25} {'Check-in Time'}")
		print(f"{'─' * 14} {'─' * 35} {'─' * 12} {'─' * 12} {'─' * 16} {'─' * 25} {'─' * 20}")
		for r in checked_in:
			print(f"{r['employee']:<14} {r['employee_name'][:34]:<35} {r['staff_type']:<12} {r['location']:<12} {r['sales_order'][:15]:<16} {r['reporting_manager'][:24]:<25} {r['checkin_time']}")
	else:
		print("  (none)")

	# Section 2: No Team Leader Error
	print(f"\n{'─' * 120}")
	print(f"  NO TEAM LEADER ERROR ({len(no_team_leader_error)})")
	print(f"{'─' * 120}")
	if no_team_leader_error:
		print(f"{'Employee':<14} {'Name':<35} {'Staff Type':<12} {'Location':<12} {'Sales Order':<16} {'Manager':<25} {'Error Time'}")
		print(f"{'─' * 14} {'─' * 35} {'─' * 12} {'─' * 12} {'─' * 16} {'─' * 25} {'─' * 20}")
		for r in no_team_leader_error:
			print(f"{r['employee']:<14} {r['employee_name'][:34]:<35} {r['staff_type']:<12} {r['location']:<12} {r['sales_order'][:15]:<16} {r['reporting_manager'][:24]:<25} {r['error_time']}")
	else:
		print("  (none)")

	# Section 3: Not Attempted
	print(f"\n{'─' * 120}")
	print(f"  NOT ATTEMPTED CHECK-IN ({len(not_attempted)})")
	print(f"{'─' * 120}")
	if not_attempted:
		print(f"{'Employee':<14} {'Name':<35} {'Staff Type':<12} {'Location':<12} {'Sales Order':<16} {'Manager':<25}")
		print(f"{'─' * 14} {'─' * 35} {'─' * 12} {'─' * 12} {'─' * 16} {'─' * 25}")
		for r in not_attempted:
			print(f"{r['employee']:<14} {r['employee_name'][:34]:<35} {r['staff_type']:<12} {r['location']:<12} {r['sales_order'][:15]:<16} {r['reporting_manager'][:24]:<25}")
	else:
		print("  (none)")

	print(f"\n{'=' * 120}")

	return {
		"date": str(date),
		"total": total,
		"summary": {
			"checked_in": len(checked_in),
			"no_team_leader_error": len(no_team_leader_error),
			"not_attempted": len(not_attempted),
		},
		"checked_in": checked_in,
		"no_team_leader_error": no_team_leader_error,
		"not_attempted": not_attempted,
	}
