# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document

class OTPLEmployeeGroup(Document):
	pass


@frappe.whitelist()
def fetch_employees(filters=None):
	if isinstance(filters, str):
		filters = frappe.parse_json(filters)

	query_filters = {}

	if filters.get("business_vertical") and filters["business_vertical"] != "ALL":
		query_filters["business_vertical"] = filters["business_vertical"]

	if filters.get("staff_type"):
		query_filters["staff_type"] = filters["staff_type"]

	if filters.get("location"):
		query_filters["location"] = filters["location"]

	if filters.get("is_team_leader"):
		query_filters["is_team_leader"] = 1

	employees = frappe.get_all(
		"Employee",
		filters=query_filters,
		fields=["name", "employee_name"]
	)

	return employees