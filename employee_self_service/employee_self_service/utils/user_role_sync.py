# -*- coding: utf-8 -*-
# Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe


def sync_employee_fields_from_user_roles(doc, method):
	"""
	Sync employee fields based on user roles
	- is_team_leader: Set if user has "TEAM LEADER" role
	- no_check_in: Set if user has "No Check In" role
	"""
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
	employee_doc = frappe.get_doc("Employee", employee)
	employee_doc.is_team_leader = is_team_leader
	employee_doc.no_check_in = no_check_in
	employee_doc.show_sales_order = show_sales_order
	employee_doc.save(ignore_permissions=True)
