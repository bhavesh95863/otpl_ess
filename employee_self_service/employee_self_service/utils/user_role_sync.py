# -*- coding: utf-8 -*-
# Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from employee_self_service.employee_self_service.utils.erp_sync import sync_employee_to_remote


def sync_employee_fields_from_user_roles(doc, method):
	"""
	Sync employee fields based on user roles
	- is_team_leader: Set if user has "TEAM LEADER" role
	- no_check_in: Set if user has "No Check In" role
	- show_sales_order: Set if user has "Show Sales Order" role
	"""
	# Prevent infinite recursion: User save → Employee save → User save → ...
	if getattr(frappe.flags, "syncing_employee_from_user_roles", False):
		return

	if not doc.name:
		return

	# Find employee linked to this user
	employee = frappe.db.get_value("Employee", {"user_id": doc.name}, "name")

	if not employee:
		return

	# Get user roles
	user_roles = frappe.get_roles(doc.name)

	# Check for specific roles
	is_team_leader = 1 if "TEAM LEADER" in user_roles else 0
	no_check_in = 1 if "No Check In" in user_roles else 0
	show_sales_order = 1 if "Show Sales Order" in user_roles else 0

	# Load the employee doc, update fields, and save to trigger all hooks (including on_update)
	emp_doc = frappe.get_doc("Employee", employee)

	# Only save if something actually changed
	if (emp_doc.is_team_leader == is_team_leader
		and emp_doc.no_check_in == no_check_in
		and emp_doc.show_sales_order == show_sales_order):
		return

	emp_doc.is_team_leader = is_team_leader
	emp_doc.no_check_in = no_check_in
	emp_doc.show_sales_order = show_sales_order

	try:
		frappe.flags.syncing_employee_from_user_roles = True
		emp_doc.save(ignore_permissions=True)
	finally:
		frappe.flags.syncing_employee_from_user_roles = False
