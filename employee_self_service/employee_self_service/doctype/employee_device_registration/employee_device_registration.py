# -*- coding: utf-8 -*-
# Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document

class EmployeeDeviceRegistration(Document):
	def validate(self):
		self.update_status_from_employee()

	def update_status_from_employee(self):
		"""Fetch and set status from the linked Employee's status."""
		if self.employee:
			employee_status = frappe.db.get_value("Employee", self.employee, "status")
			if employee_status:
				self.status = employee_status


def update_device_registration_status(doc, method=None):
	"""Called on Employee on_update to sync status to Employee Device Registration."""
	registrations = frappe.get_all(
		"Employee Device Registration",
		filters={"employee": doc.name},
		fields=["name"]
	)
	for reg in registrations:
		frappe.db.set_value("Employee Device Registration", reg.name, "status", doc.status)


def update_device_registration_app_version(doc, method=None):
	"""Called on Employee Device Info on_update/after_insert to sync app_version to Employee Device Registration."""
	if not doc.user or not doc.app_version:
		return

	# Find the employee linked to this user
	employee = frappe.db.get_value("Employee", {"user_id": doc.user}, "name")
	if not employee:
		return

	registrations = frappe.get_all(
		"Employee Device Registration",
		filters={"employee": employee},
		fields=["name"]
	)
	for reg in registrations:
		frappe.db.set_value("Employee Device Registration", reg.name, "app_version", doc.app_version)
