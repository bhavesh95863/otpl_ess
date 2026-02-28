# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

"""
Worker Attendance Module
========================

Handles attendance for employees with staff_type="Worker".

Two modes based on location:

1. Worker + Site:
   - If any Employee Checkin exists for the day → Present
   - No checkout or working hours needed
   - No checkin → Absent

2. Worker + NOT Site (e.g. Noida, Haridwar):
   - Holiday check: can only check in if Allowed Overtime form exists
     with overtime_allowed=Yes, otherwise rejected with "You are on Leave Today"
   - Early check-in: exact time recorded only if early_entry_allowed=Yes
     in Allowed Overtime, otherwise adjusted to ESS Location shift_start_time
   - Late check-out: exact time recorded only if late_exit_allowed=Yes
     in Allowed Overtime, otherwise adjusted to ESS Location shift_end_time
   - Both check-in and check-out exist → Present with working hours
   - Check-in only, no check-out → Absent
   - No check-in → Absent
"""

from __future__ import unicode_literals
import frappe
from frappe.utils import getdate, get_datetime, now_datetime, time_diff_in_hours, add_days
from datetime import datetime, time


def run_worker_attendance(employee, location, date):
	"""
	Main entry point for Worker attendance processing (called from daily_attendance).

	Routes to site or non-site handler based on location.
	Returns: Processed, Skipped, or Absent
	"""
	if location == "Site":
		return _process_worker_site(employee, date)
	else:
		return _process_worker_non_site(employee, location, date)


# ──────────────────────────────────────────────
# Worker + Site
# ──────────────────────────────────────────────

def _process_worker_site(employee, date):
	"""
	Worker + Site: If any checkin exists for the day → Present.
	No checkout or working hours needed.
	No checkin → Absent.
	"""
	from employee_self_service.employee_self_service.utils.daily_attendance import create_attendance_record

	has_checkin = frappe.db.exists(
		"Employee Checkin",
		{
			"employee": employee,
			"time": ["between", [date, add_days(date, 1)]]
		}
	)

	if has_checkin:
		create_attendance_record(
			employee=employee,
			date=date,
			status="Present",
			late_entry=False,
			early_exit=False,
			working_hours=0,
			remarks="Worker (Site) - Check-in recorded"
		)
		return "Processed"
	else:
		create_attendance_record(
			employee=employee,
			date=date,
			status="Absent",
			late_entry=False,
			early_exit=False,
			working_hours=0,
			remarks="Worker (Site) - No check-in recorded"
		)
		return "Absent"


# ──────────────────────────────────────────────
# Worker + NOT Site (e.g. Noida, Haridwar)
# ──────────────────────────────────────────────

def _process_worker_non_site(employee, location, date):
	"""
	Worker + NOT Site attendance rules:
	- On holidays: process only if Allowed Overtime exists with overtime_allowed=Yes
	- Both check-in and check-out → Present with working hours
	- Only check-in (no check-out) → Absent
	- No check-in → Absent
	"""
	from employee_self_service.employee_self_service.utils.daily_attendance import create_attendance_record

	# Holiday check: if holiday and no Allowed Overtime, skip (employee is on leave)
	if is_holiday_for_employee(employee, date):
		allowed_overtime = get_allowed_overtime(employee, date)
		if not allowed_overtime or allowed_overtime.overtime_allowed != "Yes":
			return "Skipped"

	# Get checkin records for the day
	checkins = frappe.get_all(
		"Employee Checkin",
		filters={
			"employee": employee,
			"time": ["between", [date, add_days(date, 1)]]
		},
		fields=["time", "log_type", "approval_required", "approved"],
		order_by="time asc"
	)

	# If any checkin requires approval and is not approved yet, skip
	for checkin in checkins:
		if checkin.get("approval_required") and not checkin.get("approved"):
			return "Skipped"

	# Extract first IN and last OUT
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

	# No check-in at all → Absent
	if not checkin_time:
		create_attendance_record(
			employee=employee,
			date=date,
			status="Absent",
			late_entry=False,
			early_exit=False,
			working_hours=0,
			remarks="Worker - No check-in recorded"
		)
		return "Absent"

	# Check-in exists but no check-out → Absent
	if not checkout_time:
		create_attendance_record(
			employee=employee,
			date=date,
			status="Absent",
			late_entry=False,
			early_exit=False,
			working_hours=0,
			remarks="Worker - Check-in only, no check-out recorded"
		)
		return "Absent"

	# Both check-in and check-out exist → Present with working hours
	working_hours = 0
	try:
		working_hours = time_diff_in_hours(
			get_datetime(checkout_time),
			get_datetime(checkin_time)
		)
		if working_hours < 0:
			working_hours = 0
	except:
		working_hours = 0

	create_attendance_record(
		employee=employee,
		date=date,
		status="Present",
		late_entry=False,
		early_exit=False,
		working_hours=working_hours,
		remarks="Worker attendance - {0} hours".format(round(working_hours, 2))
	)
	return "Processed"


# ──────────────────────────────────────────────
# Check-in time validation (called at check-in time from otpl_attendance)
# ──────────────────────────────────────────────

def validate_worker_checkin(employee, log_type, checkin_time=None):
	"""
	Validate check-in/check-out for Worker staff type with location != "Site".
	Called at check-in time via otpl_attendance.after_employee_checkin_insert.

	Rules:
	- Holiday: Requires Allowed Overtime with overtime_allowed=Yes
	- Early check-in (before shift start): actual time recorded only if
	  early_entry_allowed=Yes, otherwise adjusted to shift_start_time
	- Late check-out (after shift end): actual time recorded only if
	  late_exit_allowed=Yes, otherwise adjusted to shift_end_time

	Returns: (is_valid, message, adjusted_time)
	"""
	# Get employee details
	emp_doc = frappe.get_doc("Employee", employee)
	staff_type = emp_doc.staff_type
	location = emp_doc.location

	# Only apply validations for Workers with location other than "Site"
	if staff_type != "Worker" or location == "Site":
		return True, "", checkin_time

	if not checkin_time:
		checkin_time = now_datetime()

	checkin_datetime = get_datetime(checkin_time)
	checkin_date = getdate(checkin_time)
	checkin_time_only = checkin_datetime.time()

	# Get ESS Location settings for shift times
	location_settings = None
	if location and frappe.db.exists("ESS Location", location):
		location_settings = frappe.get_doc("ESS Location", location)

	# If no location settings, allow check-in/out without adjustment
	if not location_settings:
		return True, "", checkin_time

	# Get shift timings from ESS Location
	shift_start = location_settings.shift_start_time
	shift_end = location_settings.shift_end_time

	# Convert timedelta to time objects for comparison
	if shift_start and not isinstance(shift_start, time):
		shift_start = (datetime.min + shift_start).time()
	if shift_end and not isinstance(shift_end, time):
		shift_end = (datetime.min + shift_end).time()

	# --- Holiday check ---
	if is_holiday_for_employee(employee, checkin_date):
		allowed_overtime = get_allowed_overtime(employee, checkin_date)
		if not allowed_overtime or allowed_overtime.overtime_allowed != "Yes":
			return False, "You are on Leave Today", None

	# --- Early check-in (before shift start) ---
	if log_type == "IN" and shift_start and checkin_time_only < shift_start:
		allowed_overtime = get_allowed_overtime(employee, checkin_date)
		if not allowed_overtime or allowed_overtime.early_entry_allowed != "Yes":
			# Adjust check-in time to shift start
			adjusted_datetime = datetime.combine(checkin_date, shift_start)
			return True, "Early check-in not allowed. Check-in recorded at {0}".format(
				shift_start.strftime("%I:%M %p")
			), adjusted_datetime

	# --- Late check-out (after shift end) ---
	if log_type == "OUT" and shift_end and checkin_time_only > shift_end:
		allowed_overtime = get_allowed_overtime(employee, checkin_date)
		if not allowed_overtime or allowed_overtime.late_exit_allowed != "Yes":
			# Adjust check-out time to shift end
			adjusted_datetime = datetime.combine(checkin_date, shift_end)
			return True, "Late check-out not allowed. Check-out recorded at {0}".format(
				shift_end.strftime("%I:%M %p")
			), adjusted_datetime

	return True, "", checkin_time


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def get_allowed_overtime(employee, date):
	"""Get Allowed Overtime entry for employee on given date"""
	return frappe.db.get_value(
		"Allowed Overtime",
		{
			"employee": employee,
			"date": date
		},
		["name", "overtime_allowed", "early_entry_allowed", "late_exit_allowed"],
		as_dict=True
	)


def is_holiday_for_employee(employee, date):
	"""Check if date is a holiday for employee based on their holiday list or company default"""
	try:
		from erpnext.hr.doctype.holiday_list.holiday_list import is_holiday as check_holiday

		emp = frappe.get_doc("Employee", employee)

		# Check employee's own holiday list first
		if emp.holiday_list:
			return check_holiday(emp.holiday_list, date)

		# Fall back to company's default holiday list
		if emp.company:
			holiday_list = frappe.get_cached_value("Company", emp.company, "default_holiday_list")
			if holiday_list:
				return check_holiday(holiday_list, date)
	except:
		pass

	return False
