# Copyright (c) 2023, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

class EmployeeDeviceInfo(Document):
	def on_update(self):
		self.sync_app_version_to_device_registration()

	def after_insert(self):
		self.sync_app_version_to_device_registration()

	def sync_app_version_to_device_registration(self):
		"""Update app_version in Employee Device Registration based on user -> employee mapping."""
		from employee_self_service.employee_self_service.doctype.employee_device_registration.employee_device_registration import update_device_registration_app_version
		update_device_registration_app_version(self)
