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


def _is_internal_on_leave(employee, on_date):
	"""True if Employee has an Approved & submitted Leave Application covering on_date."""
	if not employee or not on_date:
		return False
	return bool(
		frappe.db.exists(
			"Leave Application",
			{
				"employee": employee,
				"status": "Approved",
				"docstatus": 1,
				"from_date": ["<=", on_date],
				"to_date": [">=", on_date],
			},
		)
	)


def _next_internal_node(employee):
	"""Given an internal Employee, return the next manager node going up.

	If the employee's manager is external (``external_reporting_manager`` set
	with ``external_report_to``), return an external node. Otherwise return the
	internal ``reports_to``. Returns None if no manager is configured.
	"""
	row = frappe.db.get_value(
		"Employee",
		employee,
		["reports_to", "external_reporting_manager", "external_report_to"],
		as_dict=True,
	)
	if not row:
		return None
	if row.get("external_reporting_manager") and row.get("external_report_to"):
		return {"type": "external", "pull_name": row.get("external_report_to")}
	if row.get("reports_to"):
		return {"type": "internal", "employee": row.get("reports_to")}
	return None


def _next_external_node(pull_row):
	"""Given a current Employee Pull row, return the next manager node going up.

	Field semantics on Employee Pull:
	* ``reports_to`` stores a remote employee id. Escalation continues in the
	  external org, so we look up another local Employee Pull whose
	  ``employee`` matches. → external node.
	* ``external_reports_to`` stores ``"<employee_id>-<company>"`` (the naming
	  pattern of an Employee Pull). The chain crosses back into the internal
	  hierarchy, so we resolve to a local ``Employee``. We read the Pull's
	  ``employee`` field (rather than parsing the string, since employee ids
	  may themselves contain hyphens) and fall back to suffix-stripping if the
	  Pull row is missing. → internal node.

	Returns None if neither field yields a usable target.
	"""
	# 1. reports_to → next Pull in the external chain
	if pull_row.get("reports_to"):
		local_pull = frappe.db.get_value(
			"Employee Pull",
			{"employee": pull_row.get("reports_to")},
			"name",
		)
		if local_pull:
			return {"type": "external", "pull_name": local_pull}

	# 2. external_reports_to → cross over to internal Employee
	ext = pull_row.get("external_reports_to")
	if ext:
		emp_id = frappe.db.get_value("Employee Pull", ext, "employee")
		if not emp_id:
			# Pull row missing locally — strip the trailing "-<company>" segment.
			emp_id = ext.rsplit("-", 1)[0]
		if emp_id and frappe.db.exists("Employee", emp_id):
			return {"type": "internal", "employee": emp_id}

	return None


def resolve_approver_chain(node, on_date, max_depth=4, strict=False):
	"""Walk the approver chain up to ``max_depth`` levels, skipping managers
	who are currently on leave on ``on_date``.

	``node`` is one of:
	  - {"type": "internal", "employee": <Employee name>}
	  - {"type": "external", "pull_name": <Employee Pull name>}

	Internal managers are considered on leave if an approved Leave Application
	covers ``on_date``. External managers are considered on leave when their
	Employee Pull ``leave_status`` is "On Leave".

	Returns the first non-on-leave node found.

	If no available approver is found within ``max_depth``:
	  * ``strict=True``  → return None so the caller can throw a clear error.
	  * ``strict=False`` → return the last visited node as a best-effort fallback
	    (legacy behaviour).

	External results include the resolved Pull row under the ``pull`` key.
	"""
	current = node
	last_visited = None
	# Initial + max_depth escalations
	for _ in range(max_depth + 1):
		if not current:
			break

		if current.get("type") == "internal":
			employee = current.get("employee")
			if not employee:
				break
			resolved = {"type": "internal", "employee": employee}
			last_visited = resolved
			if not _is_internal_on_leave(employee, on_date):
				return resolved
			current = _next_internal_node(employee)

		elif current.get("type") == "external":
			pull = get_employee_pull(current.get("pull_name"))
			if not pull:
				break
			resolved = {"type": "external", "pull_name": pull.get("name"), "pull": pull}
			last_visited = resolved
			if (pull.get("leave_status") or "") != "On Leave":
				return resolved
			current = _next_external_node(pull)

		else:
			break

	if strict:
		return None
	return last_visited


def resolve_external_manager_pull(pull_name, on_date=None, max_depth=4):
	"""Return the Employee Pull row that should approve, escalating up to
	``max_depth`` levels (including transitions across linked Pull records)
	when the current manager is On Leave.

	Kept for backwards compatibility with existing callers — internally
	delegates to :func:`resolve_approver_chain`.
	"""
	if not pull_name:
		return None
	result = resolve_approver_chain(
		{"type": "external", "pull_name": pull_name},
		on_date=on_date,
		max_depth=max_depth,
	)
	if not result:
		return None
	if result.get("type") == "external":
		return result.get("pull")
	# Chain crossed into an internal node — caller expects a Pull row, so
	# fall back to the originally requested Pull.
	return get_employee_pull(pull_name)
