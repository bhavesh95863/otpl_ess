# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt
"""Mobile APIs for the Travelling CL feature (Phase 6).

Contract for the app:
  * is_out_of_location_enabled  -> whether the logged-in employee's staff type is
    travelling-enabled at their location (so the app can show/hide the flow).
  * apply_travelling_cl         -> raise a Travelling CL (employee is the session
    user; approver is resolved server-side like OTPL Leave).
  * get_my_travelling_cl_list   -> the employee's own Travelling CLs.
  * get_travelling_cl_approvals -> Travelling CLs awaiting the manager (session
    user) — the queue the manager approves BEFORE approving check-ins.
  * set_travelling_cl_status    -> manager approves / rejects a Travelling CL.

The check-in approval sequencing itself lives in approve_employee_checkin, which
returns redirect_to='travelling_cl' while the covering request is still Pending.
"""

import json
import frappe
from frappe.utils import getdate

from employee_self_service.mobile.v1.api_utils import (
	gen_response,
	ess_validate,
	get_employee_by_user,
	exception_handler,
)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def is_out_of_location_enabled():
	"""True when the logged-in employee's staff type is travelling-enabled at their
	location (or is a Site Worker, who are always enabled)."""
	try:
		emp = get_employee_by_user(frappe.session.user, ["name", "location", "staff_type"])
		if not emp:
			return gen_response(500, "Employee does not exist")

		location, staff_type = emp.get("location"), emp.get("staff_type")
		from employee_self_service.employee_self_service.doctype.travelling_cl.travelling_cl import (
			is_travel_enabled_staff_type,
		)
		site_worker = (location == "Site" and staff_type == "Worker")
		enabled = site_worker or is_travel_enabled_staff_type(location, staff_type)
		return gen_response(200, "success", {
			"enabled": bool(enabled),
			"site_worker": bool(site_worker),
			"staff_type": staff_type,
			"location": location,
		})
	except Exception as e:
		return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_travelling_purposes():
	"""List the Travelling Purpose master values for the Purpose dropdown."""
	try:
		rows = frappe.get_all("Travelling Purpose", fields=["name as purpose"], order_by="purpose asc")
		return gen_response(200, "success", rows)
	except Exception as e:
		return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def apply_travelling_cl(*args, **kwargs):
	"""Raise a Travelling CL for the logged-in employee. Body: from_date, to_date,
	purpose (optional, a Travelling Purpose), additional_notes (optional),
	attachment (optional file URL). Approver is resolved server-side."""
	try:
		emp = get_employee_by_user(frappe.session.user)
		if not emp:
			return gen_response(500, "Employee does not exist")

		data = kwargs or json.loads(frappe.request.get_data() or "{}")
		doc = frappe.get_doc({
			"doctype": "Travelling CL",
			"employee": emp.get("name"),
			"from_date": data.get("from_date"),
			"to_date": data.get("to_date"),
			"purpose": data.get("purpose"),
			"additional_notes": data.get("additional_notes"),
			"attachment": data.get("attachment"),
			"status": "Pending",
		})
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		return gen_response(200, "Travelling CL submitted", {"name": doc.name, "status": doc.status})
	except Exception as e:
		return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_my_travelling_cl_list(start=0, page_length=20, status=None):
	"""The logged-in employee's own Travelling CLs, newest first."""
	try:
		emp = get_employee_by_user(frappe.session.user)
		if not emp:
			return gen_response(500, "Employee does not exist")

		filters = {"employee": emp.get("name")}
		if status:
			filters["status"] = status
		rows = frappe.get_all(
			"Travelling CL",
			filters=filters,
			fields=["name", "from_date", "to_date", "number_of_days", "purpose",
			        "additional_notes", "attachment", "status", "approver_name", "approver_mobile_no"],
			order_by="creation desc",
			start=int(start), page_length=int(page_length),
		)
		return gen_response(200, "success", rows)
	except Exception as e:
		return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_travelling_cl_approvals(start=0, page_length=20):
	"""Pending Travelling CLs whose approver is the logged-in manager (internal
	report_to). This is the queue to clear BEFORE approving the related check-ins."""
	try:
		emp = get_employee_by_user(frappe.session.user)
		if not emp:
			return gen_response(500, "Employee does not exist")

		rows = frappe.get_all(
			"Travelling CL",
			filters={"status": "Pending", "report_to": emp.get("name")},
			fields=["name", "employee", "employee_name", "applicant_mobile_no",
			        "from_date", "to_date", "number_of_days", "purpose", "additional_notes",
			        "attachment", "status"],
			order_by="creation desc",
			start=int(start), page_length=int(page_length),
		)
		return gen_response(200, "success", rows)
	except Exception as e:
		return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_travelling_cl_approved_list(start=0, page_length=10):
	"""Travelling CLs the logged-in manager has APPROVED — the manager's separate
	"Approved" tab (like OTPL Leave / Expense / Travel). Includes internal
	Travelling CL and external Travelling CL Pull, newest first."""
	try:
		emp = get_employee_by_user(frappe.session.user)
		if not emp:
			return gen_response(500, "Employee does not exist")
		emp_name = emp.get("name")

		internal = []
		if emp_name:
			internal = frappe.get_all(
				"Travelling CL",
				filters={"status": "Approved", "report_to": emp_name},
				fields=["name", "employee", "employee_name", "department", "from_date", "to_date",
				        "number_of_days", "purpose", "additional_notes", "attachment", "status",
				        "report_to", "remarks", "modified"],
			)
			for item in internal:
				item["doctype"] = "Travelling CL"

		pull = []
		if emp_name:
			pull = frappe.get_all(
				"Travelling CL Pull",
				filters=[
					["source_erp", "is", "set"],
					["status", "=", "Approved"],
					["report_to", "=", emp_name],
				],
				fields=["name", "employee", "employee_name", "department", "from_date", "to_date",
				        "number_of_days", "purpose", "additional_notes", "status",
				        "report_to", "remarks", "modified"],
			)
			for item in pull:
				item["doctype"] = "Travelling CL Pull"

		combined = internal + pull
		combined.sort(key=lambda x: x.get("modified") or "", reverse=True)
		start, page_length = int(start), int(page_length)
		page = combined[start:start + page_length]
		for item in page:
			item.pop("modified", None)

		return gen_response(200, "Approved Travelling CL list retrieved successfully", page)
	except Exception as e:
		return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def set_travelling_cl_status(*args, **kwargs):
	"""Manager approves / rejects a Travelling CL. Body: name, status
	(Approved|Rejected), remarks (optional), from_date / to_date (optional — the
	manager may change the requested range at approval time; they default to what
	the employee requested). Rejecting auto-rejects the linked out-of-location
	check-ins (handled in the controller)."""
	try:
		emp = get_employee_by_user(frappe.session.user)
		if not emp:
			return gen_response(500, "Employee does not exist")

		data = kwargs or json.loads(frappe.request.get_data() or "{}")
		name, status = data.get("name"), data.get("status")
		if not name or status not in ("Approved", "Rejected"):
			return gen_response(500, "name and a valid status (Approved/Rejected) are required")
		if not frappe.db.exists("Travelling CL", name):
			return gen_response(500, "Travelling CL does not exist")

		doc = frappe.get_doc("Travelling CL", name)
		# Only the resolved approver may act (internal report_to = this manager).
		if doc.report_to and doc.report_to != emp.get("name"):
			return gen_response(500, "You are not authorized to action this Travelling CL")

		# The manager may adjust the travelling range at approval; defaults stay as
		# the employee requested when not provided.
		if data.get("from_date"):
			doc.from_date = data.get("from_date")
		if data.get("to_date"):
			doc.to_date = data.get("to_date")

		doc.status = status
		if data.get("remarks"):
			doc.remarks = data.get("remarks")
		doc.flags.ignore_permissions = True
		doc.save(ignore_permissions=True)
		return gen_response(200, "Travelling CL {0}".format(status.lower()), {
			"name": doc.name, "status": doc.status,
			"from_date": str(doc.from_date), "to_date": str(doc.to_date),
			"number_of_days": doc.number_of_days,
		})
	except Exception as e:
		return exception_handler(e)
