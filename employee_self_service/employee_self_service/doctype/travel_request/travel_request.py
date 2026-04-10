# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import date_diff, getdate, nowdate
from employee_self_service.employee_self_service.utils.erp_sync import push_travel_to_remote_erp


class TravelRequest(Document):
	def validate(self):
		self.validate_dates()
		self.validate_date_conflict()
		self.calculate_number_of_days()
		self.set_approver()

	def on_update(self):
		"""Trigger sync to remote ERP when travel request has external report_to"""
		push_travel_to_remote_erp(self)
		self.update_employee_travel_status()

	def on_trash(self):
		"""Reverse employee availability/travelling changes when an approved travel request is deleted"""
		self.reverse_employee_travel_status()

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
			apply_completed_travel(self.employee, self.purpose)

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

	def validate_dates(self):
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
		if business_line_doc.reporting_manager:
			self.report_to = business_line_doc.reporting_manager
		else:
			self.report_to = None
		if business_line_doc.external_reporting_manager:
			self.has_external_report_to = 1
			self.external_report_to = business_line_doc.external_reporting_manager
		else:
			self.has_external_report_to = 0
			self.external_report_to = None


def apply_completed_travel(employee, purpose):
	"""Apply post-arrival changes for a completed travel request."""
	if purpose == "Going on Leave":
		frappe.db.set_value("Employee", employee, {"employee_availability": "On Leave", "travelling": 0})
	elif purpose == "Going back to work":
		frappe.db.set_value("Employee", employee, {"employee_availability": "", "travelling": 0})
	else:
		# Going for official work
		frappe.db.set_value("Employee", employee, "travelling", 0)


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
		fields=["name", "employee", "purpose"]
	)

	for req in active_requests:
		try:
			if req.purpose == "Going back to work":
				frappe.db.set_value("Employee", req.employee, {"employee_availability": "", "travelling": 1})
			else:
				# Going on Leave / Going for official work: just mark travelling
				travelling = frappe.db.get_value("Employee", req.employee, "travelling")
				if not travelling:
					frappe.db.set_value("Employee", req.employee, "travelling", 1)
			frappe.db.commit()
		except Exception:
			frappe.log_error(
				message=frappe.get_traceback(),
				title="Travel Request Error: {0}".format(req.name)
			)

	# Completed travel: today > arrival
	completed_requests = frappe.get_all(
		"Travel Request",
		filters={
			"status": "Approved",
			"date_of_arrival": ["<", today]
		},
		fields=["name", "employee", "purpose"]
	)

	for req in completed_requests:
		try:
			apply_completed_travel(req.employee, req.purpose)
			frappe.db.commit()
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
