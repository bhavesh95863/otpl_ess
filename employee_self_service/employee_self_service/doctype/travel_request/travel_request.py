# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import date_diff, getdate, nowdate


class TravelRequest(Document):
	def validate(self):
		self.validate_dates()
		self.calculate_number_of_days()
		self.set_approver()

	def validate_dates(self):
		if self.date_of_departure and self.date_of_arrival:
			if getdate(self.date_of_arrival) < getdate(self.date_of_departure):
				frappe.throw(_("Date of Arrival cannot be before Date of Departure"))

	def calculate_number_of_days(self):
		if self.date_of_departure and self.date_of_arrival:
			self.number_of_days = date_diff(self.date_of_arrival, self.date_of_departure) + 1

	def set_approver(self):
		if not self.employee:
			return
		employee_doc = frappe.get_doc("Employee", self.employee)
		if not employee_doc.business_vertical:
			frappe.throw(_("Employee does not have a Business Vertical assigned. Please contact HR."))
		business_line_doc = frappe.get_doc("Business Line", employee_doc.business_vertical)
		if business_line_doc.reporting_manager:
			self.report_to = business_line_doc.reporting_manager
		if business_line_doc.external_reporting_manager:
			self.has_external_report_to = 1
			self.external_report_to = business_line_doc.external_reporting_manager


def process_travel_requests():
	"""
	Scheduled task: runs daily.
	Uses date_of_departure and date_of_arrival as reference to update employee fields.
	- Approved requests where today is between departure and arrival: set employee fields based on purpose.
	- Approved requests where today is past arrival: reverse employee fields.
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
			if req.purpose == "Going on Leave":
				current = frappe.db.get_value("Employee", req.employee, "employee_availability")
				if current != "On Leave":
					frappe.db.set_value("Employee", req.employee, "employee_availability", "On Leave")
			else:
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
			if req.purpose == "Going on Leave":
				# Keep On Leave — leave continues after arrival
				pass
			elif req.purpose == "Going back to work":
				frappe.db.set_value("Employee", req.employee, {"employee_availability": "", "travelling": 0})
			else:
				frappe.db.set_value("Employee", req.employee, "travelling", 0)
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
