# -*- coding: utf-8 -*-
# Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

"""Utilities for escalating leave / approval flows when a manager is on leave."""

from __future__ import unicode_literals
import frappe


def get_employee_pull(pull_name):
	"""Fetch the relevant Employee Pull fields for escalation."""
	if not pull_name:
		return None
	return frappe.db.get_value(
		"Employee Pull",
		pull_name,
		["name", "employee", "employee_name", "mobile_no", "leave_status", "reports_to", "external_reports_to"],
		as_dict=True,
	)


def get_employee_contact(employee):
	"""Return (employee_name, mobile_no) for an Employee record."""
	if not employee:
		return None, None
	row = frappe.db.get_value(
		"Employee",
		employee,
		["employee_name", "cell_number"],
		as_dict=True,
	)
	if not row:
		return None, None
	return row.get("employee_name"), row.get("cell_number")


def get_employee_pull_contact(pull_name):
	"""Return (employee_name, mobile_no) for an Employee Pull record."""
	pull = get_employee_pull(pull_name)
	if not pull:
		return None, None
	return pull.get("employee_name"), pull.get("mobile_no")


def resolve_external_manager_pull(pull_name):
	"""Return the Employee Pull row that should approve, escalating if the
	original manager is currently On Leave.

	Escalation order when leave_status == "On Leave":
	1. The Employee Pull referenced by ``external_reports_to`` (another Pull name).
	2. The Employee Pull whose ``employee`` matches the original ``reports_to``
	   (since reports_to stores a remote Employee id).

	Falls back to the original Pull row if no escalation target is available.
	"""
	pull = get_employee_pull(pull_name)
	if not pull:
		return None

	if (pull.get("leave_status") or "") != "On Leave":
		return pull

	if pull.get("external_reports_to"):
		escalated = get_employee_pull(pull.get("external_reports_to"))
		if escalated:
			return escalated

	if pull.get("reports_to"):
		escalated_name = frappe.db.get_value(
			"Employee Pull",
			{"employee": pull.get("reports_to")},
			"name",
		)
		if escalated_name:
			escalated = get_employee_pull(escalated_name)
			if escalated:
				return escalated

	return pull
