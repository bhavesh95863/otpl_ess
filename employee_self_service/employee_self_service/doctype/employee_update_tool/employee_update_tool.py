# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.model.document import Document

class EmployeeUpdateTool(Document):
	def validate(self):
		self.validate_tables_for_update_type()
		self.validate_non_team_leader_rows()

	def validate_tables_for_update_type(self):
		if self.update_for == "Team Leader":
			if not self.employee_update_team_leader:
				frappe.throw(_("Please add at least one row in the Employee Update Team Leader table."))
			if self.employee_update_non_team_leader:
				frappe.throw(_("Employee Update Non Team Leader table must be empty when Update For is 'Team Leader'."))

		elif self.update_for == "Non Team Leader":
			if not self.employee_update_non_team_leader:
				frappe.throw(_("Please add at least one row in the Employee Update Non Team Leader table."))
			if self.employee_update_team_leader:
				frappe.throw(_("Employee Update Team Leader table must be empty when Update For is 'Non Team Leader'."))

	def validate_non_team_leader_rows(self):
		if self.update_for != "Non Team Leader":
			return

		for idx, row in enumerate(self.employee_update_non_team_leader, 1):
			if not row.employee:
				frappe.throw(_("Row {0}: Employee is mandatory in Employee Update Non Team Leader table.").format(idx))
			if not row.report_to and not row.external_report_to:
				frappe.throw(_("Row {0}: Either Report To or External Report To is mandatory in Employee Update Non Team Leader table.").format(idx))

	def on_submit(self):
		if self.update_for == "Team Leader":
			self.update_team_leader_employees()
		elif self.update_for == "Non Team Leader":
			self.update_non_team_leader_employees()

	def update_team_leader_employees(self):
		for row in self.employee_update_team_leader:
			emp = frappe.get_doc("Employee", row.employee)
			emp.sales_order = row.sales_order
			emp.save(ignore_permissions=True)

		frappe.msgprint(_("Sales Order updated for {0} employee(s).").format(len(self.employee_update_team_leader)))

	def update_non_team_leader_employees(self):
		for row in self.employee_update_non_team_leader:
			emp = frappe.get_doc("Employee", row.employee)
			if row.report_to:
				emp.reports_to = row.report_to
			if row.external_report_to:
				emp.external_report_to = row.external_report_to
			emp.save(ignore_permissions=True)

		frappe.msgprint(_("Reports To / External Report To updated for {0} employee(s).").format(len(self.employee_update_non_team_leader)))


@frappe.whitelist()
def get_employees(update_for, staff_type='', location='', business_vertical=''):
	filters = {"status": "Active"}

	if update_for == "Team Leader":
		filters["is_team_leader"] = 1
		employees = frappe.get_all("Employee", filters=filters,
			fields=["name as employee", "employee_name", "sales_order"])
		return employees

	elif update_for == "Non Team Leader":
		if staff_type:
			filters["staff_type"] = staff_type
		if location:
			filters["location"] = location
		if business_vertical:
			filters["business_vertical"] = business_vertical

		employees = frappe.get_all("Employee", filters=filters,
			fields=["name as employee", "employee_name", "reports_to as report_to", "external_report_to"])
		return employees

	return []
