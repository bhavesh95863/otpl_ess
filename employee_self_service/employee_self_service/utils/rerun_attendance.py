# -*- coding: utf-8 -*-
# Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.utils import getdate, get_datetime, add_days, get_first_day, time_diff_in_hours
from datetime import datetime


@frappe.whitelist()
def rerun_attendance_for_period(from_date=None, to_date=None):
	from_date = "2026-04-01"
	to_date = "2026-04-15"
	"""
	Re-run attendance processing for all active employees for a given date range.
	For each day in the period:
	1. Cancel and delete any existing attendance for that employee/day.
	2. If a leave application exists:
	   - If half_day and half_day_date matches → mark "Half Day"
	   - Otherwise → mark "On Leave"
	3. Else, run the same attendance logic as daily_attendance.process_employee_attendance.
	"""
	from_date = getdate(from_date)
	to_date = getdate(to_date)

	employees = frappe.get_all("Employee",
		filters={"status": "Active"},
		fields=["name", "employee_name", "location", "company", "no_check_in", "staff_type", "from_hours", "to_hours",
			"late_arrival_threshold", "early_exit_threshold", "half_day_arrival_time", "half_day_departure_time"]
	)

	total_processed = 0
	total_skipped = 0
	total_absent = 0
	total_errors = 0
	total_leave = 0
	total_cancelled = 0

	current_date = from_date
	while current_date <= to_date:
		for emp in employees:
			try:
				# Step 1: Cancel and delete existing attendance
				cancelled = cancel_and_delete_existing_attendance(emp.name, current_date)
				total_cancelled += cancelled

				# Step 2: Check leave application
				leave_result = check_and_create_leave_attendance(emp.name, current_date)
				if leave_result:
					total_leave += 1
					current_date_continue = True
				else:
					current_date_continue = False

				if current_date_continue:
					pass  # leave attendance already created, move on
				else:
					# Step 3: Re-run normal attendance logic
					result = rerun_employee_attendance(
						emp.name, emp.location, current_date,
						emp.get("no_check_in", 0), emp.get("staff_type"),
						emp.get("from_hours"), emp.get("to_hours"),
						emp.get("late_arrival_threshold"), emp.get("early_exit_threshold"),
						emp.get("half_day_arrival_time"), emp.get("half_day_departure_time")
					)

					if result == "Processed":
						total_processed += 1
					elif result == "Skipped":
						total_skipped += 1
					elif result == "Absent":
						total_absent += 1
					else:
						total_errors += 1

			except Exception as e:
				total_errors += 1
				traceback_msg = frappe.get_traceback()
				frappe.log_error(
					title="Rerun Attendance Error: {0} on {1}".format(emp.name, current_date),
					message=traceback_msg
				)
				from employee_self_service.employee_self_service.doctype.attendance_creation_failed_log.attendance_creation_failed_log import log_attendance_creation_failure
				log_attendance_creation_failure(
					employee=emp.name,
					date=current_date,
					reason="Rerun attendance error: {0}".format(str(e)),
					error_log=traceback_msg
				)

		current_date = add_days(current_date, 1)

	summary = (
		"Rerun Attendance Completed for {0} to {1}\n"
		"Cancelled: {2}, Processed: {3}, Leave: {4}, Absent: {5}, Skipped: {6}, Errors: {7}, Total Employees: {8}"
	).format(from_date, to_date, total_cancelled, total_processed, total_leave, total_absent, total_skipped, total_errors, len(employees))
	frappe.logger().info(summary)

	return {
		"from_date": str(from_date),
		"to_date": str(to_date),
		"cancelled": total_cancelled,
		"processed": total_processed,
		"leave": total_leave,
		"absent": total_absent,
		"skipped": total_skipped,
		"errors": total_errors,
		"total_employees": len(employees)
	}


def cancel_and_delete_existing_attendance(employee, date):
	"""Cancel and delete all existing attendance records for employee on given date."""
	cancelled_count = 0
	attendance_records = frappe.get_all(
		"Attendance",
		filters={
			"employee": employee,
			"attendance_date": date
		},
		fields=["name", "docstatus"]
	)

	for att in attendance_records:
		try:
			att_doc = frappe.get_doc("Attendance", att.name)
			if att_doc.docstatus == 1:
				att_doc.cancel()
			if att_doc.docstatus in (0, 2):
				frappe.delete_doc("Attendance", att.name, force=True)
			cancelled_count += 1
		except Exception as e:
			frappe.log_error(
				title="Cancel Attendance Error: {0} - {1}".format(employee, date),
				message=frappe.get_traceback()
			)

	if cancelled_count:
		frappe.db.commit()

	return cancelled_count


def check_and_create_leave_attendance(employee, date):
	"""
	Check if a leave application exists for the employee on this date.
	If yes, call the Leave Application's own update_attendance() method
	so that attendance is created with proper leave_type, leave_application
	references — same as when the leave is submitted.
	Returns True if leave attendance was created, False otherwise.
	"""
	leave_app_name = frappe.db.sql("""
		SELECT name
		FROM `tabLeave Application`
		WHERE employee = %s
		AND from_date <= %s
		AND to_date >= %s
		AND docstatus = 1
		ORDER BY modified DESC
		LIMIT 1
	""", (employee, date, date), as_dict=True)

	if not leave_app_name:
		return False

	leave_doc = frappe.get_doc("Leave Application", leave_app_name[0].name)
	leave_doc.update_attendance()
	return True


def rerun_employee_attendance(employee, location, date, no_check_in=0, staff_type=None, from_hours=None, to_hours=None,
	emp_late_arrival_threshold=None, emp_early_exit_threshold=None, emp_half_day_arrival_time=None, emp_half_day_departure_time=None):
	"""
	Process attendance for a single employee (same logic as daily_attendance.process_employee_attendance)
	but WITHOUT the existing attendance / leave skip checks (already handled by caller).
	"""
	from employee_self_service.employee_self_service.utils.daily_attendance import (
		create_attendance_record,
		is_holiday_for_company,
		determine_status,
	)

	# --- Worker employees: delegate to run_worker_attendance ---
	if staff_type == "Worker":
		from employee_self_service.employee_self_service.utils.worker_attendance import run_worker_attendance
		return run_worker_attendance(employee, location, date)

	# --- Driver + Noida: delegate to run_driver_attendance ---
	if staff_type == "Driver" and location == "Noida":
		from employee_self_service.employee_self_service.utils.driver_attendance import run_driver_attendance
		return run_driver_attendance(employee, date)

	# --- Field staff: checkin exists → Present, no checkin → Absent ---
	if staff_type == "Field":
		from employee_self_service.employee_self_service.utils.daily_attendance import _process_field_attendance
		return _process_field_attendance(employee, date)

	# --- Non-Worker: no_check_in employees are auto-marked Present ---
	is_holiday_for_company_flag = is_holiday_for_company(date)
	if no_check_in and not is_holiday_for_company_flag:
		create_attendance_record(
			employee=employee,
			date=date,
			status="Present",
			late_entry=False,
			early_exit=False,
			working_hours=0,
			remarks="Auto marked present (No check-in required)",
			checkin_time=None,
			checkout_time=None
		)
		return "Processed"

	# --- Non-Worker: standard attendance processing ---
	checkins = frappe.get_all(
		"Employee Checkin",
		filters={
			"employee": employee,
			"time": ["between", [date,date]]
		},
		fields=["time", "log_type", "approval_required", "approved", "rejected"],
		order_by="time asc"
	)

	# --- Non-Worker: skip if company holiday and no checkin records ---
	if is_holiday_for_company_flag and not checkins:
		return "Skipped"

	# Check if any checkin is pending approval
	for checkin in checkins:
		if checkin.get("approval_required") and not checkin.get("approved") and not checkin.get("rejected"):
			return "Skipped"

	# Filter out rejected checkins
	checkins = [c for c in checkins if not c.get("rejected")]

	# --- Non-Worker on holiday with checkin records ---
	if is_holiday_for_company_flag and checkins:
		has_checkin = any(c.log_type == "IN" for c in checkins)
		has_checkout = any(c.log_type == "OUT" for c in checkins)
		if has_checkin and has_checkout:
			checkin_time = None
			checkout_time = None
			for log in checkins:
				if log.log_type == "IN":
					checkin_time = log.time
					break
			for log in reversed(checkins):
				if log.log_type == "OUT":
					checkout_time = log.time
					break
			create_attendance_record(
				employee=employee,
				date=date,
				status="Present",
				late_entry=False,
				early_exit=False,
				working_hours=0,
				remarks="Present on off day (holiday with check-in and check-out)",
				checkin_time=checkin_time,
				checkout_time=checkout_time
			)
			return "Processed"
		else:
			return "Skipped"

	checkin_time = None
	checkout_time = None

	if checkins:
		for log in checkins:
			if log.log_type == "IN":
				checkin_time = log.time
				break
		for log in reversed(checkins):
			if log.log_type == "OUT":
				checkout_time = log.time
				break

	# No checkin at all → Absent
	if not checkin_time and not checkout_time:
		create_attendance_record(
			employee=employee,
			date=date,
			status="Absent",
			late_entry=False,
			early_exit=False,
			working_hours=0,
			remarks="No check-in and check-out records",
			checkin_time=None,
			checkout_time=None
		)
		return "Absent"

	# Approved out-of-office handling
	if location and location != "Site" and frappe.db.exists("ESS Location", location):
		has_approved_out_of_office = any(
			c.get("approval_required") and c.get("approved")
			for c in checkins
		)
		if has_approved_out_of_office and (checkin_time or checkout_time):
			create_attendance_record(
				employee=employee,
				date=date,
				status="Present",
				late_entry=False,
				early_exit=False,
				working_hours=0,
				remarks="Present - Approved out-of-office check-in/check-out",
				checkin_time=checkin_time,
				checkout_time=checkout_time
			)
			return "Processed"

	# Non-Worker, non-Site: Absent if check-in or check-out is missing
	if location != "Site":
		if not checkin_time or not checkout_time:
			missing = "check-out" if checkin_time else "check-in"
			create_attendance_record(
				employee=employee,
				date=date,
				status="Absent",
				late_entry=False,
				early_exit=False,
				working_hours=0,
				remarks="Missing {0} (Non-Worker, Non-Site)".format(missing),
				checkin_time=checkin_time,
				checkout_time=checkout_time
			)
			return "Absent"

	# Get ESS Location rules
	location_rules = None
	if location:
		if frappe.db.exists("ESS Location", location):
			location_rules = frappe.get_doc("ESS Location", location)
			if from_hours and to_hours:
				location_rules.shift_start_time = from_hours
				location_rules.shift_end_time = to_hours
				location_rules.late_arrival_threshold = from_hours
				location_rules.early_exit_threshold = to_hours
			if emp_late_arrival_threshold:
				location_rules.late_arrival_threshold = emp_late_arrival_threshold
			if emp_early_exit_threshold:
				location_rules.early_exit_threshold = emp_early_exit_threshold
			if emp_half_day_arrival_time:
				location_rules.half_day_arrival_time = emp_half_day_arrival_time
			if emp_half_day_departure_time:
				location_rules.half_day_departure_time = emp_half_day_departure_time

	# Determine attendance status based on rules
	status, late_entry, early_exit, remarks = determine_status(
		checkin_time, checkout_time, location_rules, employee, date
	)

	working_hours = 0

	create_attendance_record(
		employee=employee,
		date=date,
		status=status,
		late_entry=late_entry,
		early_exit=early_exit,
		working_hours=working_hours,
		remarks=remarks,
		checkin_time=checkin_time,
		checkout_time=checkout_time
	)

	return "Processed"
