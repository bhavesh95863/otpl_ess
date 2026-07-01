# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import date_diff, getdate, nowdate
from employee_self_service.employee_self_service.utils.erp_sync import push_travel_to_remote_erp
from employee_self_service.employee_self_service.utils.leave_escalation import (
	resolve_external_manager_pull,
	resolve_approver_chain,
	get_employee_contact,
	get_employee_pull_contact,
)


class TravelRequest(Document):
	def validate(self):
		self.validate_dates()
		self.validate_date_conflict()
		self.calculate_number_of_days()
		self.set_approver()
		self.set_contact_details()

	def on_update(self):
		"""Trigger sync to remote ERP when travel request has external report_to"""
		push_travel_to_remote_erp(self)
		self.update_employee_travel_status()
		self.mark_present_for_travel_period()

	def on_trash(self):
		"""Reverse employee availability/travelling changes when an approved travel request is deleted"""
		self.reverse_employee_travel_status()

	def mark_present_for_travel_period(self):
		"""When the request is Approved, convert any 'Absent' attendance to
		'Present' for the travel period — from Date of Departure up to today,
		but never beyond Date of Arrival.

		Example: 25-06 -> 28-06 approved on 26-06 marks 25 & 26 present now; the
		daily scheduler flips 27 & 28 as those days pass. If approved on/after
		30-06, the whole 25-28 range is corrected at once. Only flips
		Absent -> Present, so it is safe to run on every save while Approved.
		"""
		if self.status != "Approved" or not self.employee:
			return
		if not self.date_of_departure or not self.date_of_arrival:
			return
		mark_travel_dates_present(
			self.employee, self.date_of_departure, self.date_of_arrival, travel_request=self.name
		)

	def update_employee_travel_status(self):
		"""
		On approval, immediately update employee fields based on purpose:
		- Going on Leave: mark travelling (availability changes to On Leave only after arrival)
		- Going back to work: clear availability (was On Leave), mark travelling
		- Going for official work: mark travelling (no availability change)
		"""
		if self.status != "Approved" or not self.employee:
			return
		if not self.date_of_departure or not self.date_of_arrival:
			return

		today = getdate(nowdate())
		departure = getdate(self.date_of_departure)
		arrival = getdate(self.date_of_arrival)

		if departure <= today <= arrival:
			# Active travel period
			if self.purpose == "Going back to work":
				frappe.db.set_value("Employee", self.employee, {"employee_availability": "", "travelling": 1})
			else:
				# Going on Leave / Going for official work: just mark travelling
				frappe.db.set_value("Employee", self.employee, "travelling", 1)
		elif today > arrival:
			# Already past arrival
			apply_completed_travel(self.name, self.employee, self.purpose)

	def reverse_employee_travel_status(self):
		"""Reverse changes made by this travel request on deletion."""
		if self.status != "Approved" or not self.employee:
			return
		if not self.date_of_departure or not self.date_of_arrival:
			return

		today = getdate(nowdate())
		departure = getdate(self.date_of_departure)
		arrival = getdate(self.date_of_arrival)

		if departure <= today <= arrival:
			# Travel is currently active — reverse active changes
			if self.purpose == "Going back to work":
				frappe.db.set_value("Employee", self.employee, {"employee_availability": "On Leave", "travelling": 0})
			else:
				# Going on Leave / Going for official work: was just travelling
				frappe.db.set_value("Employee", self.employee, "travelling", 0)
		elif today > arrival:
			# Travel already completed — reverse completed changes
			if self.purpose == "Going on Leave":
				frappe.db.set_value("Employee", self.employee, {"employee_availability": "", "travelling": 0})
			elif self.purpose == "Going back to work":
				frappe.db.set_value("Employee", self.employee, "employee_availability", "On Leave")
			# Clear the one-shot flag so it can be re-applied if a new request is created later
			frappe.db.set_value("Travel Request", self.name, "post_arrival_processed", 0)

	def validate_dates(self):
		# "Start date must be today or later" — enforced only on first save
		# (creation), so that editing/approving an existing request whose
		# (originally valid) departure date has since passed is not blocked.
		if self.is_new() and self.date_of_departure:
			if getdate(self.date_of_departure) < getdate(nowdate()):
				frappe.throw(_("Date of Departure cannot be in the past. Please select today or a later date."))

		if self.date_of_departure and self.date_of_arrival:
			if getdate(self.date_of_arrival) < getdate(self.date_of_departure):
				frappe.throw(_("Date of Arrival cannot be before Date of Departure"))

	def validate_date_conflict(self):
		if not self.employee or not self.date_of_departure or not self.date_of_arrival:
			return

		filters = {
			"employee": self.employee,
			"name": ["!=", self.name],
			"status": ["not in", ["Cancelled", "Rejected"]],
			"date_of_departure": ["<=", self.date_of_arrival],
			"date_of_arrival": [">=", self.date_of_departure],
		}

		overlapping = frappe.get_all(
			"Travel Request",
			filters=filters,
			fields=["name", "date_of_departure", "date_of_arrival"],
			limit=1,
		)

		if overlapping:
			req = overlapping[0]
			frappe.throw(
				_("Travel Request dates overlap with existing request {0} ({1} to {2})").format(
					req.name, req.date_of_departure, req.date_of_arrival
				)
			)

	def calculate_number_of_days(self):
		if self.date_of_departure and self.date_of_arrival:
			self.number_of_days = date_diff(self.date_of_arrival, self.date_of_departure) + 1

	def set_approver(self):
		if not self.employee:
			return
		employee_doc = frappe.get_doc("Employee", self.employee)
		if not employee_doc.business_vertical and not employee_doc.external_business_vertical:
			frappe.throw(_("Employee does not have a Business Vertical assigned. Please contact HR."))
		business_vertical = employee_doc.business_vertical or employee_doc.external_business_vertical
		business_line_doc = frappe.get_doc("Business Line", business_vertical)

		# Use date_of_departure (falling back to today) for leave checks so the
		# approver chain reflects who will be available when the request is raised.
		on_date = getdate(self.date_of_departure) if self.date_of_departure else getdate(nowdate())

		# Reset before resolving — the chain may flip between internal/external
		# (e.g. an internal manager on leave whose own manager is external).
		self.report_to = None
		self.has_external_report_to = 0
		self.external_report_to = None

		if business_line_doc.reporting_manager:
			resolved = resolve_approver_chain(
				{"type": "internal", "employee": business_line_doc.reporting_manager},
				on_date=on_date,
				strict=True,
			)
			if resolved:
				if resolved.get("type") == "internal":
					self.report_to = resolved.get("employee")
				else:
					self.has_external_report_to = 1
					self.external_report_to = resolved.get("pull_name")

		if business_line_doc.external_reporting_manager and not self.has_external_report_to and not self.report_to:
			resolved = resolve_approver_chain(
				{"type": "external", "pull_name": business_line_doc.external_reporting_manager},
				on_date=on_date,
				strict=True,
			)
			if resolved:
				if resolved.get("type") == "external":
					self.has_external_report_to = 1
					self.external_report_to = resolved.get("pull_name")
				else:
					# Chain crossed into an internal employee; only set if not
					# already set by the internal branch above.
					if not self.report_to:
						self.report_to = resolved.get("employee")

		if not self.report_to and not self.has_external_report_to:
			frappe.throw(
				_("No available approver found — all managers in the escalation chain are on leave. Please contact HR.")
			)

	def set_contact_details(self):
		"""Populate applicant and approver contact details (name + mobile).

		Cannot use fetch_from since approver may be either an internal Employee
		(report_to) or an external Employee Pull record (external_report_to).
		"""
		# Applicant
		applicant_name, applicant_mobile = get_employee_contact(self.employee)
		if applicant_name and not self.employee_name:
			self.employee_name = applicant_name
		self.applicant_mobile_no = applicant_mobile or ""

		# Approver
		approver_name = None
		approver_mobile = None
		if self.has_external_report_to and self.external_report_to:
			approver_name, approver_mobile = get_employee_pull_contact(self.external_report_to)
		elif self.report_to:
			approver_name, approver_mobile = get_employee_contact(self.report_to)
		self.approver_name = approver_name or ""
		self.approver_mobile_no = approver_mobile or ""

	def _is_on_leave(self, employee):
		"""Check whether the given employee has an approved Leave Application
		covering this travel request's date_of_departure."""
		if not employee or not self.date_of_departure:
			return False
		today = getdate(nowdate())
		return bool(
			frappe.db.exists(
				"Leave Application",
				{
					"employee": employee,
					"status": "Approved",
					"docstatus": 1,
					"from_date": ["<=", today],
					"to_date": [">=", today],
				},
			)
		)


def apply_completed_travel(travel_request_name, employee, purpose):
	"""Apply post-arrival changes for a completed travel request — ONCE.

	Uses the `post_arrival_processed` flag on the Travel Request to ensure
	the transition (e.g. setting employee_availability='On Leave' for
	'Going on Leave') runs exactly once on the day after Date of Arrival,
	not every subsequent daily run.
	"""
	if frappe.db.get_value("Travel Request", travel_request_name, "post_arrival_processed"):
		return False

	if purpose == "Going on Leave":
		frappe.db.set_value("Employee", employee, {"employee_availability": "On Leave", "travelling": 0})
	elif purpose == "Going back to work":
		frappe.db.set_value("Employee", employee, {"employee_availability": "", "travelling": 0})
	else:
		# Going for official work
		frappe.db.set_value("Employee", employee, "travelling", 0)

	frappe.db.set_value("Travel Request", travel_request_name, "post_arrival_processed", 1)
	return True


def mark_travel_dates_present(employee, date_of_departure, date_of_arrival, travel_request=None):
	"""Convert 'Absent' attendance to 'Present' for an approved travel period,
	from ``date_of_departure`` up to today (inclusive), capped at
	``date_of_arrival``.

	Only existing 'Absent' records are flipped; missing days are NOT created and
	other statuses (Present / Half Day / On Leave) are left untouched. Cancelled
	attendance (docstatus=2) is ignored. Idempotent — safe to re-run.

	Each flipped record gets a timeline comment noting it was set Present because
	of ``travel_request``, so the change is auditable on the Attendance form.
	"""
	if not employee or not date_of_departure or not date_of_arrival:
		return

	start = getdate(date_of_departure)
	end = min(getdate(date_of_arrival), getdate(nowdate()))
	if start > end:
		# Travel hasn't started yet — nothing in the past to correct.
		return

	absent_records = frappe.get_all(
		"Attendance",
		filters={
			"employee": employee,
			"attendance_date": ["between", [start, end]],
			"status": "Absent",
			"docstatus": ["<", 2],
		},
		fields=["name", "attendance_date"],
	)

	note = "Marked <b>Present</b> (was Absent) by Travel Request <b>{0}</b> ({1} to {2}).".format(
		travel_request or "N/A", getdate(date_of_departure), getdate(date_of_arrival)
	)
	for att in absent_records:
		frappe.db.set_value("Attendance", att.name, "status", "Present")
		try:
			# Timeline note so it's clear WHY this day became Present. A note
			# failure must never block the attendance correction itself.
			frappe.get_doc("Attendance", att.name).add_comment("Comment", text=note)
		except Exception:
			frappe.log_error(
				message=frappe.get_traceback(),
				title="Travel attendance note failed: {0}".format(att.name),
			)


def process_travel_requests():
	"""
	Scheduled task: runs daily.
	Uses date_of_departure and date_of_arrival as reference to update employee fields.

	Active travel (departure <= today <= arrival):
	  - Going on Leave: mark travelling=1 (availability stays blank during travel)
	  - Going back to work: clear availability, mark travelling=1
	  - Going for official work: mark travelling=1 (no availability change)

	Completed travel (today > arrival):
	  - Going on Leave: set availability=On Leave, travelling=0
	  - Going back to work: set availability='', travelling=0
	  - Going for official work: travelling=0 (no availability change)
	"""
	today = getdate(nowdate())

	# Active travel: departure <= today <= arrival
	active_requests = frappe.get_all(
		"Travel Request",
		filters={
			"status": "Approved",
			"date_of_departure": ["<=", today],
			"date_of_arrival": [">=", today]
		},
		fields=["name", "employee", "purpose", "date_of_departure", "date_of_arrival"]
	)
	active_employees = {req.employee for req in active_requests}

	# Completed travel: today > arrival
	# Process completed FIRST so active travel below can override any
	# stale `travelling=0` reset for employees who are still travelling.
	# Only fetch requests that have NOT been processed yet — the post-arrival
	# transition (e.g. 'Going on Leave' -> employee_availability='On Leave')
	# must run exactly once, the first run after Date of Arrival.
	completed_requests = frappe.get_all(
		"Travel Request",
		filters={
			"status": "Approved",
			"date_of_arrival": ["<", today],
			"post_arrival_processed": 0
		},
		fields=["name", "employee", "purpose"]
	)

	for req in completed_requests:
		try:
			# Skip if employee currently has an active travel — the active
			# loop below will set the correct in-travel state. Mark this old
			# completed request as processed so it never fires again.
			if req.employee in active_employees:
				frappe.db.set_value("Travel Request", req.name, "post_arrival_processed", 1)
				continue
			apply_completed_travel(req.name, req.employee, req.purpose)
		except Exception:
			frappe.log_error(
				message=frappe.get_traceback(),
				title="Travel Request Error: {0}".format(req.name)
			)

	for req in active_requests:
		try:
			# Keep attendance consistent with the trip: flip any Absent to
			# Present from departure up to today (capped at arrival). This
			# extends the window each day for still-ongoing travel.
			mark_travel_dates_present(
				req.employee, req.date_of_departure, req.date_of_arrival, travel_request=req.name
			)
			if req.purpose in ["Going back to work", "Going for official work"]:
				frappe.db.set_value("Employee", req.employee, {"employee_availability": "", "travelling": 1})
			else:
				# Going on Leave / Going for official work: just mark travelling
				travelling = frappe.db.get_value("Employee", req.employee, "travelling")
				if not travelling:
					frappe.db.set_value("Employee", req.employee, "travelling", 1)
		except Exception:
			frappe.log_error(
				message=frappe.get_traceback(),
				title="Travel Request Error: {0}".format(req.name)
			)


def is_employee_travelling(employee):
	"""
	Check if an employee has an active approved travel request
	(current date is between date_of_departure and date_of_arrival inclusive).
	Returns the travel request name if found, else None.
	"""
	today = getdate(nowdate())
	travel_request = frappe.db.get_value(
		"Travel Request",
		{
			"employee": employee,
			"status": "Approved",
			"date_of_departure": ["<=", today],
			"date_of_arrival": [">=", today]
		},
		"name"
	)
	return travel_request
