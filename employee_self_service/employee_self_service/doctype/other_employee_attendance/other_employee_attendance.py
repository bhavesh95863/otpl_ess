# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.model.document import Document

class OtherEmployeeAttendance(Document):
	def after_insert(self):
		self.create_employee_checkin()

	def create_employee_checkin(self):
		employee_checkin = frappe.get_doc({
			"doctype": "Employee Checkin",
			"employee": self.employee,
			"time": self.attendance_datetime,
			"log_type": self.attendance_type,
			"location": self.location
		})
		employee_checkin.insert(ignore_permissions=True)
