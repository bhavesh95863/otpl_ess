# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt
"""Mobile APIs for OTPL Leave Amendment (amend an approved leave when the employee
returns early). Contract for the app:
  * get_amendable_leaves          -> the employee's Approved full-day leaves that
    can be amended (not Short Leave / Half Day).
  * apply_leave_amendment         -> raise an amendment (shorter range).
  * get_my_leave_amendments       -> the employee's own amendments.
  * get_leave_amendment_approvals -> pending amendments for the manager.
  * get_leave_amendment_approved_list -> the manager's approved amendments.
  * set_leave_amendment_status    -> manager approves / rejects.
"""

import json
import frappe

from employee_self_service.mobile.v1.api_utils import (
	gen_response,
	ess_validate,
	get_employee_by_user,
	exception_handler,
)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_amendable_leaves(start=0, page_length=20):
	"""Approved, full-day OTPL Leaves of the logged-in employee that may be amended
	(Short Leave and Half Day are excluded)."""
	try:
		emp = get_employee_by_user(frappe.session.user)
		if not emp:
			return gen_response(500, "Employee does not exist")
		rows = frappe.get_all(
			"OTPL Leave",
			filters={
				"employee": emp.get("name"),
				"status": "Approved",
				"short_leave": 0,
				"half_day": 0,
			},
			fields=["name", "approved_from_date as from_date", "approved_to_date as to_date",
			        "total_no_of_approved_days as number_of_days", "reason"],
			order_by="approved_from_date desc",
			start=int(start), page_length=int(page_length),
		)
		return gen_response(200, "success", rows)
	except Exception as e:
		return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def apply_leave_amendment(*args, **kwargs):
	"""Raise a Leave Amendment. Body: original_leave, amended_from_date,
	amended_to_date, reason. Approver is resolved server-side."""
	try:
		emp = get_employee_by_user(frappe.session.user)
		if not emp:
			return gen_response(500, "Employee does not exist")
		data = kwargs or json.loads(frappe.request.get_data() or "{}")
		doc = frappe.get_doc({
			"doctype": "OTPL Leave Amendment",
			"employee": emp.get("name"),
			"original_leave": data.get("original_leave"),
			"amended_from_date": data.get("amended_from_date"),
			"amended_to_date": data.get("amended_to_date"),
			"reason": data.get("reason"),
			"status": "Pending",
		})
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		return gen_response(200, "Leave amendment submitted", {"name": doc.name, "status": doc.status})
	except Exception as e:
		return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_my_leave_amendments(start=0, page_length=20, status=None):
	"""The logged-in employee's own leave amendments, newest first."""
	try:
		emp = get_employee_by_user(frappe.session.user)
		if not emp:
			return gen_response(500, "Employee does not exist")
		filters = {"employee": emp.get("name")}
		if status:
			filters["status"] = status
		rows = frappe.get_all(
			"OTPL Leave Amendment",
			filters=filters,
			fields=["name", "original_leave", "original_from_date", "original_to_date",
			        "amended_from_date", "amended_to_date", "number_of_days", "reason",
			        "status", "amended_leave", "approver_name", "approver_mobile_no"],
			order_by="creation desc",
			start=int(start), page_length=int(page_length),
		)
		return gen_response(200, "success", rows)
	except Exception as e:
		return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_leave_amendment_approvals(start=0, page_length=20):
	"""Pending leave amendments awaiting the logged-in manager (internal approver)."""
	try:
		emp = get_employee_by_user(frappe.session.user)
		if not emp:
			return gen_response(500, "Employee does not exist")
		rows = frappe.get_all(
			"OTPL Leave Amendment",
			filters={"status": "Pending", "report_to": emp.get("name")},
			fields=["name", "employee", "employee_name", "applicant_mobile_no", "original_leave",
			        "original_from_date", "original_to_date", "amended_from_date", "amended_to_date",
			        "number_of_days", "reason", "status"],
			order_by="creation desc",
			start=int(start), page_length=int(page_length),
		)
		return gen_response(200, "success", rows)
	except Exception as e:
		return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_leave_amendment_approved_list(start=0, page_length=10):
	"""Leave amendments the logged-in manager has Approved (the manager's Approved tab)."""
	try:
		emp = get_employee_by_user(frappe.session.user)
		if not emp:
			return gen_response(500, "Employee does not exist")
		rows = frappe.get_all(
			"OTPL Leave Amendment",
			filters={"status": "Approved", "report_to": emp.get("name")},
			fields=["name", "employee", "employee_name", "original_leave", "amended_leave",
			        "original_from_date", "original_to_date", "amended_from_date", "amended_to_date",
			        "number_of_days", "reason", "status", "remarks"],
			order_by="modified desc",
			start=int(start), page_length=int(page_length),
		)
		return gen_response(200, "Approved leave amendment list retrieved successfully", rows)
	except Exception as e:
		return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def set_leave_amendment_status(*args, **kwargs):
	"""Manager approves / rejects a leave amendment. Body: name, status
	(Approved|Rejected), remarks (optional). Approving cancels the original leave
	and creates the amended (shorter) leave server-side."""
	try:
		emp = get_employee_by_user(frappe.session.user)
		if not emp:
			return gen_response(500, "Employee does not exist")
		data = kwargs or json.loads(frappe.request.get_data() or "{}")
		name, status = data.get("name"), data.get("status")
		if not name or status not in ("Approved", "Rejected"):
			return gen_response(500, "name and a valid status (Approved/Rejected) are required")
		if not frappe.db.exists("OTPL Leave Amendment", name):
			return gen_response(500, "Leave amendment does not exist")
		doc = frappe.get_doc("OTPL Leave Amendment", name)
		if doc.report_to and doc.report_to != emp.get("name"):
			return gen_response(500, "You are not authorized to action this amendment")
		doc.status = status
		if data.get("remarks"):
			doc.remarks = data.get("remarks")
		doc.flags.ignore_permissions = True
		doc.save(ignore_permissions=True)
		doc.reload()
		return gen_response(200, "Leave amendment {0}".format(status.lower()), {
			"name": doc.name, "status": doc.status, "amended_leave": doc.amended_leave,
		})
	except Exception as e:
		return exception_handler(e)
