# -*- coding: utf-8 -*-
# Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.utils import getdate, get_datetime, add_days, get_first_day, time_diff_in_hours
from datetime import datetime, timedelta


@frappe.whitelist()
def process_daily_attendance():
	"""
	Scheduled job to process attendance for all employees.
	Runs at midnight for previous day.

	Simplified Rules:
	1. Any employee with no Employee Checkin for the day → Absent
	2. Worker + Site: checkin exists → Present (no checkout/hours needed)
	3. Worker + NOT Site: uses Allowed Overtime rules (see run_worker_attendance)
	4. Non-Worker: existing ESS Location-based rules for late/half-day
	"""
	yesterday = add_days(getdate(), -1)

	employees = frappe.get_all("Employee",
		filters={"status": "Active"},
		fields=["name", "employee_name", "location", "company", "no_check_in", "staff_type","from_hours","to_hours",
			"late_arrival_threshold", "early_exit_threshold", "half_day_arrival_time", "half_day_departure_time"]
	)

	processed_count = 0
	skipped_count = 0
	error_count = 0
	absent_count = 0

	for emp in employees:
		try:
			result = process_employee_attendance(
				emp.name, emp.location, yesterday,
				emp.get("no_check_in", 0), emp.get("staff_type"), emp.get("from_hours"), emp.get("to_hours"),
				emp.get("late_arrival_threshold"), emp.get("early_exit_threshold"),
				emp.get("half_day_arrival_time"), emp.get("half_day_departure_time")
			)

			if result == "Processed":
				processed_count += 1
			elif result == "Skipped":
				skipped_count += 1
			elif result == "Absent":
				absent_count += 1
			else:
				error_count += 1

		except Exception as e:
			error_count += 1
			traceback_msg = frappe.get_traceback()
			frappe.log_error(
				title="Daily Attendance Processing Error: {0}".format(emp.name),
				message=traceback_msg
			)
			from employee_self_service.employee_self_service.doctype.attendance_creation_failed_log.attendance_creation_failed_log import log_attendance_creation_failure
			log_attendance_creation_failure(
				employee=emp.name,
				date=yesterday,
				reason="Daily attendance processing error: {0}".format(str(e)),
				error_log=traceback_msg
			)

	# Log summary
	summary = "Attendance Processing Completed for {0}\\nProcessed: {1}, Absent: {2}, Skipped: {3}, Errors: {4}, Total: {5}".format(
		yesterday, processed_count, absent_count, skipped_count, error_count, len(employees)
	)
	frappe.logger().info(summary)

	return {
		"date": str(yesterday),
		"processed": processed_count,
		"absent": absent_count,
		"skipped": skipped_count,
		"errors": error_count,
		"total": len(employees)
	}


def process_employee_attendance(employee, location, date, no_check_in=0, staff_type=None, from_hours=None, to_hours=None,
	emp_late_arrival_threshold=None, emp_early_exit_threshold=None, emp_half_day_arrival_time=None, emp_half_day_departure_time=None):
	"""
	Process attendance for a single employee.
	Returns: Processed, Skipped, Absent, or Error

	Routing:
	- Attendance or leave application already exists → Skipped
	- Worker employees → delegated to run_worker_attendance
	- no_check_in employees → auto Present
	- Non-Worker on holiday → Skipped
	- Non-Worker, non-Site → check-in + check-out required, ESS Location late/early rules, working hours
	"""
	# Check if attendance already exists (submitted) or leave application exists
	existing_attendance = frappe.db.get_value(
		"Attendance",
		{
			"employee": employee,
			"attendance_date": date,
			"docstatus": 1
		},
		"name"
	)

	if existing_attendance:
		# Attendance already created for this day, skip
		return "Skipped"

	# Check if leave application exists for this day
	existing_leave = frappe.db.exists(
		"Leave Application",
		{
			"employee": employee,
			"from_date": ["<=", date],
			"to_date": [">=", date],
			"docstatus": 1
		}
	)

	if existing_leave:
		# Leave application exists for this day, skip
		return "Skipped"

	# --- Worker employees: delegate to run_worker_attendance ---
	if staff_type == "Worker":
		from employee_self_service.employee_self_service.utils.worker_attendance import run_worker_attendance
		return run_worker_attendance(employee, location, date)

	# --- Driver + Noida: delegate to run_driver_attendance ---
	if staff_type == "Driver" and location == "Noida":
		from employee_self_service.employee_self_service.utils.driver_attendance import run_driver_attendance
		return run_driver_attendance(employee, date)

	# --- Field staff: checkin exists → Present, no checkin → Absent (irrespective of location) ---
	if staff_type == "Field":
		return _process_field_attendance(employee, date)

	# --- Non-Worker: no_check_in employees are auto-marked Present ---
	is_holiday_for_company_flag = is_holiday_for_company(date)
	print(f"Processing Non-Worker {employee} on {date} | no_check_in: {no_check_in} | Holiday: {is_holiday_for_company}")
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
	# Get checkin records for the employee
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

	# Check if any checkin is pending approval (not yet approved or rejected)
	for checkin in checkins:
		if checkin.get("approval_required") and not checkin.get("approved") and not checkin.get("rejected"):
			return "Skipped"

	# Filter out rejected checkins — treat them as if they don't exist
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
			# Checkin exists but no checkout on holiday → skip (shows as Off Day in calendar)
			return "Skipped"

	checkin_time = None
	checkout_time = None
	checkin_out_of_location = False
	checkout_out_of_location = False

	if checkins:
		# Get first IN (and whether it was an approved out-of-location punch)
		for log in checkins:
			if log.log_type == "IN":
				checkin_time = log.time
				checkin_out_of_location = bool(log.get("approval_required") and log.get("approved"))
				break

		# Get last OUT (and whether it was an approved out-of-location punch)
		for log in reversed(checkins):
			if log.log_type == "OUT":
				checkout_time = log.time
				checkout_out_of_location = bool(log.get("approval_required") and log.get("approved"))
				break

	# Rule 1: No checkin at all → Absent
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

	# Get ESS Location rules (incl. employee overrides + short-leave adjustment)
	location_rules = build_location_rules(
		location, date, employee, from_hours, to_hours,
		emp_late_arrival_threshold, emp_early_exit_threshold,
		emp_half_day_arrival_time, emp_half_day_departure_time
	)

	# Non-Site out-of-location approved punches use the standard shift time so
	# the out-of-location punch is not treated as late / early.
	checkin_time, checkout_time = apply_out_of_location_shift_times(
		checkin_time, checkout_time,
		checkin_out_of_location, checkout_out_of_location,
		location_rules, date
	)

	# Determine attendance status based on rules
	status, late_entry, early_exit, extra_late_entry, extra_early_exit, remarks = determine_status(
		checkin_time, checkout_time, location_rules, employee, date
	)

	# Non-Worker employees do not need working hours
	working_hours = 0

	# Create attendance record
	create_attendance_record(
		employee=employee,
		date=date,
		status=status,
		late_entry=late_entry,
		early_exit=early_exit,
		working_hours=working_hours,
		remarks=remarks,
		checkin_time=checkin_time,
		checkout_time=checkout_time,
		extra_late_entry=extra_late_entry,
		extra_early_exit=extra_early_exit
	)

	return "Processed"


def _process_field_attendance(employee, date):
	"""
	Field staff attendance: irrespective of location.
	If any checkin exists for the day → Present (no checkout/hours needed).
	No checkin → Absent.
	"""
	has_checkin = frappe.db.sql(
		"""SELECT name FROM `tabEmployee Checkin`
		WHERE employee = %s AND time >= %s AND time < %s
		LIMIT 1""",
		(employee, date, add_days(date, 1))
	)

	if has_checkin:
		checkin_record = frappe.db.sql(
			"""SELECT time FROM `tabEmployee Checkin`
			WHERE employee = %s AND time >= %s AND time < %s
			ORDER BY time ASC LIMIT 1""",
			(employee, date, add_days(date, 1)),
			as_dict=True
		)
		checkin_time = checkin_record[0].time if checkin_record else None

		create_attendance_record(
			employee=employee,
			date=date,
			status="Present",
			late_entry=False,
			early_exit=False,
			working_hours=0,
			remarks="Field Staff - Check-in recorded",
			checkin_time=checkin_time,
			checkout_time=None
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
			remarks="Field Staff - No check-in recorded",
			checkin_time=None,
			checkout_time=None
		)
		return "Absent"


def determine_status(checkin_time, checkout_time, location_rules, employee, date):
	"""
	Determine attendance status based on checkin/checkout times and ESS Location rules.
	Used for non-Worker employees only.

	Late / early handling has three tiers:
	- Present, on time: no flags.
	- Present, late/early past late_arrival_threshold / early_exit_threshold:
	  late_entry / early_exit is set on EVERY such day (the "allowed"/grace
	  marker). extra_late_entry / extra_early_exit is added only on the specific
	  "deduction" days, i.e. when the running monthly tally of late/early days
	  equals late_count_for_half_day, equals late_count_for_full_day, or exceeds
	  treat_late_as_half_day_after. late_entry / early_exit stay set on those
	  days too.
	- Half Day: crossing the stricter half_day_arrival_time /
	  half_day_departure_time marks the day Half Day, counted purely as Half Day
	  (late_entry / early_exit and the extra flags are left unset).

	Returns: (status, late_entry, early_exit, extra_late_entry, extra_early_exit, remarks)
	"""
	status = "Present"
	late_entry = False
	early_exit = False
	extra_late_entry = False
	extra_early_exit = False
	remarks_list = []

	# If no location rules, just mark missing logs as late
	if not location_rules:
		if not checkin_time:
			late_entry = True
			remarks_list.append("Missing check-in")
		if not checkout_time:
			early_exit = True
			remarks_list.append("Missing check-out")

		remarks = ", ".join(remarks_list) if remarks_list else "Regular attendance"
		return status, late_entry, early_exit, extra_late_entry, extra_early_exit, remarks

	# Get thresholds from location rules
	try:
		late_threshold = None
		early_exit_threshold = None
		half_day_arrival = None
		half_day_departure = None

		if location_rules.late_arrival_threshold:
			late_threshold = datetime.strptime(
				str(location_rules.late_arrival_threshold), "%H:%M:%S"
			).time()

		if location_rules.early_exit_threshold:
			early_exit_threshold = datetime.strptime(
				str(location_rules.early_exit_threshold), "%H:%M:%S"
			).time()

		if location_rules.half_day_arrival_time:
			half_day_arrival = datetime.strptime(
				str(location_rules.half_day_arrival_time), "%H:%M:%S"
			).time()

		if location_rules.half_day_departure_time:
			half_day_departure = datetime.strptime(
				str(location_rules.half_day_departure_time), "%H:%M:%S"
			).time()

		# Check for missing logs - always mark as late
		if not checkin_time:
			late_entry = True
			remarks_list.append("Missing check-in")

		if not checkout_time:
			early_exit = True
			remarks_list.append("Missing check-out")

		# Tier 2 (Half Day): crossing the stricter half-day arrival / departure
		# time marks the day Half Day. Such a day is counted purely as Half Day,
		# so the late_entry / early_exit and extra flags are left unset.
		is_half_day = False
		if checkin_time and half_day_arrival:
			if get_datetime(checkin_time).time() >= half_day_arrival:
				is_half_day = True
				remarks_list.append("Half Day - arrived at/after {0}".format(half_day_arrival))

		if checkout_time and half_day_departure:
			if get_datetime(checkout_time).time() <= half_day_departure:
				is_half_day = True
				remarks_list.append("Half Day - left at/before {0}".format(half_day_departure))

		if is_half_day:
			status = "Half Day"
			late_entry = False
			early_exit = False
			extra_late_entry = False
			extra_early_exit = False
		else:
			# Present tier: late arrival / early exit past the regular threshold
			# sets late_entry / early_exit on EVERY such day (the "allowed"/grace
			# marker).
			if checkin_time and late_threshold:
				if get_datetime(checkin_time).time() > late_threshold:
					late_entry = True
					remarks_list.append("Late arrival after {0}".format(late_threshold))

			if checkout_time and early_exit_threshold:
				if get_datetime(checkout_time).time() < early_exit_threshold:
					early_exit = True
					remarks_list.append("Early exit before {0}".format(early_exit_threshold))

			# The extra flag marks the specific "deduction" days, based on the
			# running monthly tally of late/early days. It fires when that tally:
			#   - equals late_count_for_half_day, or
			#   - equals late_count_for_full_day, or
			#   - exceeds treat_late_as_half_day_after.
			# late_entry / early_exit stay set on these days too.
			if late_entry or early_exit:
				half_count = int(location_rules.get('late_count_for_half_day') or 0)
				full_count = int(location_rules.get('late_count_for_full_day') or 0)
				treat_after = int(location_rules.get('treat_late_as_half_day_after') or 0)
				new_late_count = get_month_late_count(employee, date) + 1
				is_extra = (
					(half_count and new_late_count == half_count)
					or (full_count and new_late_count == full_count)
					or (treat_after and new_late_count > treat_after)
				)
				if is_extra:
					if late_entry:
						extra_late_entry = True
					if early_exit:
						extra_early_exit = True
					remarks_list.append(
						"Extra mark (late/early day #{0} this month)".format(new_late_count)
					)

	except Exception as e:
		traceback_msg = frappe.get_traceback()
		frappe.log_error(
			title="Determine Status Error: {0}".format(employee),
			message=traceback_msg
		)
		from employee_self_service.employee_self_service.doctype.attendance_creation_failed_log.attendance_creation_failed_log import log_attendance_creation_failure
		log_attendance_creation_failure(
			employee=employee,
			date=date,
			reason="Error determining attendance status: {0}".format(str(e)),
			error_log=traceback_msg
		)

	remarks = ", ".join(remarks_list) if remarks_list else "Regular attendance"
	return status, late_entry, early_exit, extra_late_entry, extra_early_exit, remarks


def get_month_late_count(employee, date):
	"""Get count of late marks for employee in current month up to given date"""
	try:
		month_start = get_first_day(date)

		attendance_records = frappe.get_all(
			"Attendance",
			filters={
				"employee": employee,
				"attendance_date": ["between", [month_start, add_days(date, -1)]],
				"docstatus": 1
			},
			fields=["name"]
		)

		late_count = 0
		for att in attendance_records:
			att_doc = frappe.get_doc("Attendance", att.name)
			if (hasattr(att_doc, 'late_entry') and att_doc.late_entry) or \
			   (hasattr(att_doc, 'early_exit') and att_doc.early_exit):
				late_count += 1

		return late_count
	except:
		return 0


def create_attendance_record(employee, date, status, late_entry, early_exit, working_hours, remarks, checkin_time=None, checkout_time=None, extra_late_entry=False, extra_early_exit=False):
	"""Create and submit attendance record"""
	try:
		attendance = frappe.get_doc({
			"doctype": "Attendance",
			"employee": employee,
			"attendance_date": date,
			"status": status,
			"remarks": remarks,
			"company": frappe.db.get_value("Global Defaults", "Global Defaults", "default_company") or "",
		})

		# Set late_entry and early_exit if fields exist
		if hasattr(attendance, 'late_entry'):
			attendance.late_entry = 1 if late_entry else 0
		if hasattr(attendance, 'early_exit'):
			attendance.early_exit = 1 if early_exit else 0
		if hasattr(attendance, 'extra_late_entry'):
			attendance.extra_late_entry = 1 if extra_late_entry else 0
		if hasattr(attendance, 'extra_early_exit'):
			attendance.extra_early_exit = 1 if extra_early_exit else 0
		if hasattr(attendance, 'working_hours'):
			attendance.working_hours = working_hours

		# Set checkin_time and checkout_time if fields exist
		if hasattr(attendance, 'checkin_time') and checkin_time:
			attendance.checkin_time = checkin_time
		if hasattr(attendance, 'checkout_time') and checkout_time:
			attendance.checkout_time = checkout_time

		attendance.insert(ignore_permissions=True)
		attendance.submit()

	except Exception as e:
		traceback_msg = frappe.get_traceback()
		frappe.log_error(
			title="Create Attendance Error: {0} - {1}".format(employee, date),
			message=traceback_msg
		)
		from employee_self_service.employee_self_service.doctype.attendance_creation_failed_log.attendance_creation_failed_log import log_attendance_creation_failure
		log_attendance_creation_failure(
			employee=employee,
			date=date,
			reason="Failed to create/submit attendance record: {0}".format(str(e)),
			error_log=traceback_msg
		)
		raise


def get_approved_short_leave_period(employee, date):
	"""Check if employee has an approved OTPL Leave with short_leave on this date.
	Returns the half_day_period ('First Half' or 'Second Half') or None."""
	result = frappe.db.get_value(
		"OTPL Leave",
		{
			"employee": employee,
			"short_leave": 1,
			"status": "Approved",
			"approved_from_date": ["<=", date],
			"approved_to_date": [">=", date]
		},
		"half_day_period"
	)
	return result if result else None


def adjust_thresholds_for_short_leave(location_rules, short_leave_period):
	"""Adjust location_rules timing thresholds based on short leave period.

	First Half short leave: employee is off in the morning, so start time
	shifts forward by 2 hours (e.g. 9:30 → 11:30).

	Second Half short leave: employee leaves early, so end time shifts
	back by 2 hours (e.g. 18:00 → 16:00).
	"""
	SHORT_LEAVE_OFFSET = timedelta(hours=2)

	def _shift_time(time_val, offset):
		"""Add/subtract offset from a timedelta or time string and return as timedelta."""
		if not time_val:
			return time_val
		# Convert to timedelta seconds for arithmetic
		if isinstance(time_val, timedelta):
			total = time_val
		else:
			parts = str(time_val).split(":")
			total = timedelta(hours=int(parts[0]), minutes=int(parts[1]),
							  seconds=int(parts[2]) if len(parts) > 2 else 0)
		new_total = total + offset
		# Clamp to 0–24h range
		if new_total.total_seconds() < 0:
			new_total = timedelta(0)
		if new_total.total_seconds() > 86400:
			new_total = timedelta(hours=24)
		return new_total

	if short_leave_period == "First Half":
		location_rules.shift_start_time = _shift_time(location_rules.shift_start_time, SHORT_LEAVE_OFFSET)
		location_rules.late_arrival_threshold = _shift_time(location_rules.late_arrival_threshold, SHORT_LEAVE_OFFSET)
		if location_rules.half_day_arrival_time:
			location_rules.half_day_arrival_time = _shift_time(location_rules.half_day_arrival_time, SHORT_LEAVE_OFFSET)

	elif short_leave_period == "Second Half":
		location_rules.shift_end_time = _shift_time(location_rules.shift_end_time, -SHORT_LEAVE_OFFSET)
		location_rules.early_exit_threshold = _shift_time(location_rules.early_exit_threshold, -SHORT_LEAVE_OFFSET)
		if location_rules.half_day_departure_time:
			location_rules.half_day_departure_time = _shift_time(location_rules.half_day_departure_time, -SHORT_LEAVE_OFFSET)

	return location_rules


def build_location_rules(location, date, employee, from_hours=None, to_hours=None,
	emp_late_arrival_threshold=None, emp_early_exit_threshold=None,
	emp_half_day_arrival_time=None, emp_half_day_departure_time=None):
	"""Load the ESS Location rules for an employee/date and return a ready-to-use
	rules doc.

	This is the single source of truth for how attendance thresholds are resolved.
	It applies, in order:
	  1. Employee working-hours override (from_hours / to_hours)
	  2. Employee-level threshold overrides
	  3. Any approved short-leave threshold shift for the date

	The scheduled daily job AND the reprocess/backfill paths all call this so
	short-leave handling can never drift between them.

	Returns the (possibly mutated) ESS Location doc, or None when the location
	has no ESS Location rules.
	"""
	if not location or not frappe.db.exists("ESS Location", location):
		return None

	location_rules = frappe.get_doc("ESS Location", location)
	if from_hours and to_hours:
		location_rules.shift_start_time = from_hours
		location_rules.shift_end_time = to_hours
		location_rules.late_arrival_threshold = from_hours
		location_rules.early_exit_threshold = to_hours
	# Override with employee-level thresholds if defined
	if emp_late_arrival_threshold:
		location_rules.late_arrival_threshold = emp_late_arrival_threshold
	if emp_early_exit_threshold:
		location_rules.early_exit_threshold = emp_early_exit_threshold
	if emp_half_day_arrival_time:
		location_rules.half_day_arrival_time = emp_half_day_arrival_time
	if emp_half_day_departure_time:
		location_rules.half_day_departure_time = emp_half_day_departure_time

	# Adjust thresholds if employee has an approved short leave for this date
	short_leave_period = get_approved_short_leave_period(employee, date)
	if short_leave_period:
		location_rules = adjust_thresholds_for_short_leave(location_rules, short_leave_period)

	return location_rules


def _datetime_at(date, time_val):
	"""Build a datetime for `date` at the given time-of-day.

	`time_val` may be a timedelta (how ESS Location Time fields load) or a
	'HH:MM:SS' / 'HH:MM:SS.ffffff' string.
	"""
	d = getdate(date)
	if isinstance(time_val, timedelta):
		total_seconds = int(time_val.total_seconds())
	else:
		parts = str(time_val).split(":")
		hours = int(parts[0])
		minutes = int(parts[1]) if len(parts) > 1 else 0
		seconds = int(float(parts[2])) if len(parts) > 2 else 0
		total_seconds = hours * 3600 + minutes * 60 + seconds
	return datetime(d.year, d.month, d.day) + timedelta(seconds=total_seconds)


def apply_out_of_location_shift_times(checkin_time, checkout_time,
	checkin_out_of_location, checkout_out_of_location, location_rules, date):
	"""Non-Site out-of-location handling.

	When a Non-Site employee's check-in (or check-out) punch was made out of
	location and approved, that punch's time is replaced by the standard shift
	start (or end) time from the ESS Location, so the out-of-location punch is
	not treated as late arrival / early exit. The other punch is evaluated
	normally.
	"""
	if not location_rules:
		return checkin_time, checkout_time

	if checkin_out_of_location and location_rules.get("shift_start_time"):
		checkin_time = _datetime_at(date, location_rules.shift_start_time)

	if checkout_out_of_location and location_rules.get("shift_end_time"):
		checkout_time = _datetime_at(date, location_rules.shift_end_time)

	return checkin_time, checkout_time


def is_holiday_for_company(date):
	"""Check if date is a holiday based on Global Defaults company holiday list"""
	try:
		from erpnext.hr.doctype.holiday_list.holiday_list import is_holiday as check_holiday

		# Get default company from Global Defaults
		default_company = frappe.db.get_single_value("Global Defaults", "default_company")
		if not default_company:
			return False

		# Get company's default holiday list
		holiday_list = frappe.get_cached_value("Company", default_company, "default_holiday_list")
		if not holiday_list:
			return False

		return check_holiday(holiday_list, date)
	except:
		pass

	return False

@frappe.whitelist()
def is_holiday_check_api(date):
	return is_holiday_for_company(date)