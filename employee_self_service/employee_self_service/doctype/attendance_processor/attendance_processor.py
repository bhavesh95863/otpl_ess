# -*- coding: utf-8 -*-
# Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from frappe.utils import getdate, get_datetime, add_days, get_first_day, get_last_day, time_diff_in_hours, now_datetime
from datetime import datetime, timedelta
import json

class AttendanceProcessor(Document):
	def validate(self):
		# Prevent editing of completed records
		if self.status == "Completed" and not self.is_new():
			old_doc = self.get_doc_before_save()
			if old_doc and old_doc.status == "Completed":
				frappe.throw("Cannot edit a completed Attendance Processor record")

@frappe.whitelist()
def process_attendance_manually(doc_name):
	"""Process attendance based on the Attendance Processor document"""
	doc = frappe.get_doc("Attendance Processor", doc_name)
	
	# Prevent reprocessing of completed records
	if doc.status == "Completed":
		return {"status": "error", "message": "This attendance processor has already been completed and cannot be reprocessed"}
	
	doc.status = "Processing"
	doc.processing_log = "Starting attendance processing...\n"
	doc.save()
	frappe.db.commit()
	
	try:
		if doc.process_for_all:
			employees = frappe.get_all("Employee", filters={"status": "Active"}, pluck="name")
			log = []
			for emp in employees:
				result = process_attendance_for_employee(emp, getdate(doc.date))
				log.append("{0}: {1}".format(emp, result))
			doc.processing_log += "\n".join(log)
		else:
			if doc.employee:
				result = process_attendance_for_employee(doc.employee, getdate(doc.date))
				doc.processing_log += result
		
		doc.status = "Completed"
		doc.save()
		frappe.db.commit()
		return {"status": "success", "message": "Attendance processed successfully"}
	except Exception as e:
		frappe.log_error(title="Attendance Processing Error", message=frappe.get_traceback())
		doc.status = "Failed"
		doc.processing_log += "\nError: {0}".format(str(e))
		doc.save()
		frappe.db.commit()
		return {"status": "error", "message": str(e)}

def process_attendance_for_employee(employee, date):
	"""
	Process attendance for a single employee on a specific date
	Args:
		employee: Employee ID
		date: Date to process
	"""
	try:
		date = getdate(date)
		
		# Check if it's a holiday or weekly off
		if is_holiday_or_weekly_off(employee, date):
			return "Holiday/Weekly Off - Skipped"
		
		# Check if leave application exists
		if has_leave_application(employee, date):
			return "Leave Application exists - Skipped"
		
		# Get employee location/branch
		emp_doc = frappe.get_doc("Employee", employee)
		location = get_employee_location(emp_doc)
		
		if not location:
			return "No location configured for employee"
		
		# Get location settings
		location_settings = frappe.get_doc("ESS Location", location)
		
		# Get checkins for the day
		checkin_time, checkout_time = get_employee_checkins(employee, date)
		
		# Determine attendance status
		status, late_entry, early_exit, remarks = determine_attendance_status(
			checkin_time, checkout_time, location_settings, date, employee
		)
		
		# Cancel existing attendance if not from leave application
		cancel_existing_attendance(employee, date)
		
		# Create new attendance
		attendance = create_attendance(employee, date, status, remarks, late_entry, early_exit, checkin_time, checkout_time)
		
		return "Processed - Status: {0}, Late Entry: {1}, Early Exit: {2}".format(status, late_entry, early_exit)
		
	except Exception as e:
		frappe.log_error(title="Process Attendance for Employee: {0}".format(employee), message=frappe.get_traceback())
		return "Error: {0}".format(str(e))

def get_employee_location(emp_doc):
	"""Get employee's location based on location"""
	# Get location from employee's lcation
	if emp_doc.location:
		return emp_doc.location
	
	return None

def get_employee_checkins(employee, date):
	"""Get first and last checkin for the employee on the date"""
	checkins = frappe.get_all(
		"Employee Checkin",
		filters={
			"employee": employee,
			"time": ["between", [date, add_days(date, 1)]]
		},
		fields=["time", "log_type"],
		order_by="time asc"
	)
	
	checkin_time = None
	checkout_time = None
	
	if checkins:
		# Get first IN
		for log in checkins:
			if log.log_type == "IN" and not checkin_time:
				checkin_time = log.time
				break
		
		# Get last OUT
		for log in reversed(checkins):
			if log.log_type == "OUT" and not checkout_time:
				checkout_time = log.time
				break
	
	return checkin_time, checkout_time

def determine_attendance_status(checkin_time, checkout_time, location_settings, date, employee):
	"""
	Determine attendance status based on checkin/checkout times and location rules
	Returns: (status, late_entry, early_exit, remarks)
	"""
	remarks = []
	late_entry = False
	early_exit = False
	status = "Present"
	
	if not checkin_time and not checkout_time:
		return "Absent", True, "Missing both check-in and check-out"
	
	# Check if employee has exceeded late threshold for treating as half day
	treat_as_half_day_threshold = location_settings.get('treat_late_as_half_day_after') or 5
	current_month_late_count = get_current_month_late_count(employee, date)
	should_treat_late_as_half_day = current_month_late_count >= treat_as_half_day_threshold
	
	# Convert times for comparison
	shift_start = datetime.strptime(str(location_settings.shift_start_time), "%H:%M:%S").time()
	shift_end = datetime.strptime(str(location_settings.shift_end_time), "%H:%M:%S").time()
	
	late_threshold = None
	early_exit_threshold = None
	half_day_arrival = None
	half_day_departure = None
	
	if location_settings.late_arrival_threshold:
		late_threshold = datetime.strptime(str(location_settings.late_arrival_threshold), "%H:%M:%S").time()
	
	if location_settings.early_exit_threshold:
		early_exit_threshold = datetime.strptime(str(location_settings.early_exit_threshold), "%H:%M:%S").time()
	
	if location_settings.half_day_arrival_time:
		half_day_arrival = datetime.strptime(str(location_settings.half_day_arrival_time), "%H:%M:%S").time()
	
	if location_settings.half_day_departure_time:
		half_day_departure = datetime.strptime(str(location_settings.half_day_departure_time), "%H:%M:%S").time()
	
	# Check for missing logs - always mark as late
	if not checkin_time:
		late_entry = True
		remarks.append("Missing check-in")
	
	if not checkout_time:
		early_exit = True
		remarks.append("Missing check-out")
	
	# Check for half day conditions
	if checkin_time and half_day_arrival:
		checkin_only_time = get_datetime(checkin_time).time()
		if checkin_only_time >= half_day_arrival:
			status = "Half Day"
			remarks.append("Arrived at/after {0}".format(half_day_arrival))
	
	if checkout_time and half_day_departure:
		checkout_only_time = get_datetime(checkout_time).time()
		if checkout_only_time <= half_day_departure:
			status = "Half Day"
			remarks.append("Left at/before {0}".format(half_day_departure))
	
	# Check for late arrival (if not already half day)
	if checkin_time and late_threshold and status != "Half Day":
		checkin_only_time = get_datetime(checkin_time).time()
		if checkin_only_time > late_threshold:
			if should_treat_late_as_half_day:
				status = "Half Day"
				remarks.append("Late arrival treated as Half Day (exceeded {0} late marks)".format(treat_as_half_day_threshold))
			else:
				late_entry = True
				remarks.append("Late arrival after {0}".format(late_threshold))
	
	# Check for early exit (if not already half day)
	if checkout_time and early_exit_threshold and status != "Half Day":
		checkout_only_time = get_datetime(checkout_time).time()
		if checkout_only_time < early_exit_threshold:
			if should_treat_late_as_half_day:
				status = "Half Day"
				remarks.append("Early exit treated as Half Day (exceeded {0} late marks)".format(treat_as_half_day_threshold))
			else:
				early_exit = True
				remarks.append("Early exit before {0}".format(early_exit_threshold))
	
	# Check for missing logs treated as half day after threshold
	if (late_entry or early_exit) and should_treat_late_as_half_day and status == "Present":
		status = "Half Day"
		late_entry = False
		early_exit = False
		remarks.append("Missing log treated as Half Day (exceeded {0} late marks)".format(treat_as_half_day_threshold))
	
	return status, late_entry, early_exit, ", ".join(remarks) if remarks else "Regular attendance"

def get_current_month_late_count(employee, date):
	"""Get count of late marks for employee in the current month up to the given date"""
	date = getdate(date)
	month_start = get_first_day(date)
	
	# Get all attendance records for the month up to current date (excluding current date)
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
		# Count if either late_entry or early_exit is marked
		if (hasattr(att_doc, 'late_entry') and att_doc.late_entry) or (hasattr(att_doc, 'early_exit') and att_doc.early_exit):
			late_count += 1
	
	return late_count

def create_pending_attendance_with_approval(employee, date, remarks, missing_log_type, checkin_time=None, checkout_time=None):
	"""Create pending attendance in draft mode and approval request"""
	try:
		# Check if attendance already exists
		existing_att = frappe.db.get_value("Attendance", {"employee": employee, "attendance_date": date}, "name")
		if existing_att:
			return
		
		# Create draft attendance with Pending status
		attendance = frappe.get_doc({
			"doctype": "Attendance",
			"employee": employee,
			"attendance_date": date,
			"status": "Absent",  # Set as Absent until approved
			"remarks": remarks
		})
		attendance.insert(ignore_permissions=True)
		# Keep in draft mode, don't submit
		frappe.db.commit()
		
		# Create Employee Approval request
		existing_approval = frappe.db.sql("""
			SELECT name FROM `tabEmployee Approval`
			WHERE employee = %s AND date = %s 
			AND approval_type = 'Missing Log' AND docstatus < 2
			LIMIT 1
		""", (employee, date))
		
		if not existing_approval:
			emp_approval = frappe.get_doc({
				"doctype": "Employee Approval",
				"employee": employee,
				"date": date,
				"approval_type": "Missing Log",
				"missing_log_type": missing_log_type,
				"checkin_time": checkin_time,
				"checkout_time": checkout_time,
				"workflow_state": "Pending"
			})
			emp_approval.insert(ignore_permissions=True)
			frappe.db.commit()
			
	except Exception as e:
		frappe.log_error(title="Create Pending Attendance with Approval: {0} - {1}".format(employee, date), message=frappe.get_traceback())

def create_missing_log_approval(employee, date, missing_type):
	"""Create Employee Approval request for missing log (deprecated - kept for backward compatibility)"""
	try:
		# Check if already exists
		existing_approval = frappe.db.sql("""
			SELECT name FROM `tabEmployee Approval`
			WHERE employee = %s AND date = %s 
			AND approval_type = 'Missing Log' AND docstatus < 2
			LIMIT 1
		""", (employee, date))
		
		if not existing_approval:
			emp_approval = frappe.get_doc({
				"doctype": "Employee Approval",
				"employee": employee,
				"date": date,
				"approval_type": "Missing Log",
				"missing_log_type": missing_type,
				"workflow_state": "Pending"
			})
			emp_approval.insert(ignore_permissions=True)
			frappe.db.commit()
	except Exception as e:
		frappe.log_error(title="Create Missing Log Approval: {0} - {1}".format(employee, date), message=frappe.get_traceback())

def create_attendance(employee, date, status, remarks, late_entry=False, early_exit=False, checkin_time=None, checkout_time=None):
	"""Create or update attendance record"""
	attendance = frappe.get_doc({
		"doctype": "Attendance",
		"employee": employee,
		"attendance_date": date,
		"status": status,
		"remarks": remarks
	})
	
	# Set standard late_entry and early_exit fields
	if hasattr(attendance, 'late_entry'):
		attendance.late_entry = 1 if late_entry else 0
	if hasattr(attendance, 'early_exit'):
		attendance.early_exit = 1 if early_exit else 0
	
	# Calculate and set working_hours only if both checkin and checkout times exist and are not None
	if checkin_time is not None and checkout_time is not None:
		try:
			checkin_dt = get_datetime(checkin_time)
			checkout_dt = get_datetime(checkout_time)
			
			# Only calculate if both datetime objects are valid
			if checkin_dt and checkout_dt:
				# Calculate hours difference
				working_hours = time_diff_in_hours(checkout_dt, checkin_dt)
				
				# Only set if positive value (checkout after checkin)
				if working_hours > 0 and hasattr(attendance, 'working_hours'):
					attendance.working_hours = working_hours
		except Exception as e:
			frappe.log_error(title="Calculate Working Hours Error: {0}".format(employee), message=frappe.get_traceback())
	else:
		# Explicitly set working_hours to 0 if either time is missing
		if hasattr(attendance, 'working_hours'):
			attendance.working_hours = 0
	
	attendance.insert(ignore_permissions=True)
	attendance.submit()
	frappe.db.commit()
	
	return attendance

def cancel_existing_attendance(employee, date):
	"""Cancel existing attendance if not created from leave application"""
	try:
		existing_attendance = frappe.get_all(
			"Attendance",
			filters={
				"employee": employee,
				"attendance_date": date,
				"docstatus": 1
			},
			fields=["name", "leave_application"]
		)
		
		for att in existing_attendance:
			# Don't cancel if created from leave application
			if not att.leave_application:
				att_doc = frappe.get_doc("Attendance", att.name)
				att_doc.cancel()
				frappe.db.commit()
	except Exception as e:
		frappe.log_error(title="Cancel Existing Attendance: {0} - {1}".format(employee, date), message=frappe.get_traceback())

def is_holiday_or_weekly_off(employee, date):
	"""Check if the date is a holiday or weekly off"""
	from erpnext.hr.doctype.holiday_list.holiday_list import is_holiday
	
	emp = frappe.get_doc("Employee", employee)
	if emp.holiday_list:
		return is_holiday(emp.holiday_list, date)
	
	return False

def has_leave_application(employee, date):
	"""Check if employee has approved leave application for the date"""
	leave_apps = frappe.get_all(
		"Leave Application",
		filters={
			"employee": employee,
			"from_date": ["<=", date],
			"to_date": [">=", date],
			"status": "Approved",
			"docstatus": 1
		}
	)
	return len(leave_apps) > 0

@frappe.whitelist()
def process_monthly_late_deductions():
	"""
	Process monthly late deductions for all employees
	Run at the end of each month
	"""
	from dateutil.relativedelta import relativedelta
	
	# Get previous month
	today = getdate()
	first_day = get_first_day(today)
	last_month_end = add_days(first_day, -1)
	last_month_start = get_first_day(last_month_end)
	
	# Get all active employees
	employees = frappe.get_all("Employee", filters={"status": "Active"}, pluck="name")
	
	for employee in employees:
		try:
			process_employee_monthly_deduction(employee, last_month_start, last_month_end)
		except Exception as e:
			frappe.log_error(title="Monthly Late Deduction Error: {0}".format(employee), message=frappe.get_traceback())

def process_employee_monthly_deduction(employee, from_date, to_date):
	"""Process late deductions for a single employee for the month"""
	
	# Get employee location settings
	emp_doc = frappe.get_doc("Employee", employee)
	location = get_employee_location(emp_doc)
	
	if not location:
		return
	
	location_settings = frappe.get_doc("ESS Location", location)
	
	if not location_settings.leave_type_for_deduction:
		return
	
	late_count_half = location_settings.late_count_for_half_day or 3
	late_count_full = location_settings.late_count_for_full_day or 5
	
	# Count late marks for the month
	attendance_records = frappe.get_all(
		"Attendance",
		filters={
			"employee": employee,
			"attendance_date": ["between", [from_date, to_date]],
			"docstatus": 1
		},
		fields=["name", "attendance_date", "status"]
	)
	
	late_count = 0
	for att in attendance_records:
		att_doc = frappe.get_doc("Attendance", att.name)
		# Count if either late_entry or early_exit is marked
		if (hasattr(att_doc, 'late_entry') and att_doc.late_entry) or (hasattr(att_doc, 'early_exit') and att_doc.early_exit):
			late_count += 1
	
	# Calculate leave deduction
	leave_days = 0
	if late_count >= late_count_full:
		# 5 L = 1 full day, then every additional L = 0.5 day
		leave_days = 1
		extra_lates = late_count - late_count_full
		leave_days += extra_lates * 0.5
	elif late_count >= late_count_half:
		# 3 L = 0.5 day
		leave_days = 0.5
	
	if leave_days > 0:
		# Create leave application for deduction
		create_leave_application_for_deduction(
			employee, 
			to_date, 
			leave_days, 
			location_settings.leave_type_for_deduction,
			"Auto-deducted for {0} late marks in {1}".format(late_count, to_date.strftime("%B %Y"))
		)

def create_leave_application_for_deduction(employee, date, leave_days, leave_type, description):
	"""Create leave application for late deduction"""
	try:
		# Check if already exists
		existing = frappe.db.exists("Leave Application", {
			"employee": employee,
			"from_date": date,
			"to_date": date,
			"leave_type": leave_type,
			"description": ["like", "%Auto-deducted%"],
			"docstatus": ["<", 2]
		})
		
		if existing:
			return
		
		leave_app = frappe.get_doc({
			"doctype": "Leave Application",
			"employee": employee,
			"leave_type": leave_type,
			"from_date": date,
			"to_date": date,
			"half_day": 1 if leave_days == 0.5 else 0,
			"description": description,
			"status": "Approved"
		})
		
		leave_app.insert(ignore_permissions=True)
		leave_app.submit()
		frappe.db.commit()
		
	except Exception as e:
		frappe.log_error(title="Create Leave Deduction: {0} - {1}".format(employee, date), message=frappe.get_traceback())

@frappe.whitelist()
def process_previous_day_attendance():
	"""
	Scheduled job to process previous day attendance for all employees
	Runs at midnight
	"""
	yesterday = add_days(getdate(), -1)
	
	employees = frappe.get_all("Employee", filters={"status": "Active"}, pluck="name")
	
	processed_count = 0
	skipped_count = 0
	error_count = 0
	
	for employee in employees:
		try:
			result = process_attendance_for_employee(employee, yesterday)
			if "Skipped" in result:
				skipped_count += 1
			elif "Error" in result:
				error_count += 1
			else:
				processed_count += 1
		except Exception as e:
			error_count += 1
			frappe.log_error(title="Scheduled Attendance Processing Error: {0}".format(employee), message=frappe.get_traceback())
	
	# Log summary
	summary = "Attendance Processing Completed for {0}\nProcessed: {1}, Skipped: {2}, Errors: {3}, Total Employees: {4}".format(
		yesterday, processed_count, skipped_count, error_count, len(employees)
	)
	frappe.logger().info(summary)
	
	return {"processed": processed_count, "skipped": skipped_count, "errors": error_count, "total": len(employees)}
