# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

"""
Driver Attendance Module
========================

Handles attendance for employees with staff_type="Driver" and location="Noida".

Rules:
- Check-in exists → Present
- If check-out exists, use actual check-out time
- If no check-out, default check-out time to 6:00 PM
- Calculate working hours from check-in to check-out
- Deduct 30 minutes (0.5 hours) break time when marking Present
- No check-in → Absent
"""

from __future__ import unicode_literals
import frappe
from frappe.utils import getdate, get_datetime, time_diff_in_hours, add_days
from datetime import datetime, time


def run_driver_attendance(employee, date):
	"""
	Main entry point for Driver (Noida) attendance processing.
	Called from daily_attendance.process_employee_attendance.

	Returns: Processed, Skipped, or Absent
	"""
	from employee_self_service.employee_self_service.utils.daily_attendance import create_attendance_record

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
			remarks="Driver (Noida) - No check-in recorded",
			checkin_time=None,
			checkout_time=None
		)
		return "Absent"

	# If no check-out, default to 6:00 PM
	if not checkout_time:
		checkout_time = datetime.combine(getdate(date), time(18, 0, 0))

	# Calculate working hours
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

	# Deduct 30 minutes (0.5 hours) break time
	if working_hours > 0.5:
		working_hours -= 0.5

	create_attendance_record(
		employee=employee,
		date=date,
		status="Present",
		late_entry=False,
		early_exit=False,
		working_hours=working_hours,
		remarks="Driver (Noida) - {0} hours (30 min break deducted)".format(round(working_hours, 2)),
		checkin_time=checkin_time,
		checkout_time=checkout_time
	)
	return "Processed"
