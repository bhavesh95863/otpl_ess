# -*- coding: utf-8 -*-
# Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def sync_worker_fields_before_save(doc, method=None):
	"""
	Hook for Employee doctype validate (before save)

	Case 1: If this is a Worker (staff_type="Worker" and is_team_leader=0)
	- Copy fields from their reporting manager directly onto doc so they
	  are persisted as part of the current save (no reload needed).
	"""
	if doc.get("employee_availability") == "On Leave":
		doc.reports_to = frappe.db.get_value(
			"Employee Self Service Settings",
			"Employee Self Service Settings",
			"default_reporting_manager"
		)
		doc.external_reporting_manager = 0
		doc.location = frappe.db.get_value("Employee", doc.reports_to, "location") or "Noida"

	# Case 1: Update this worker's fields from their manager
	if doc.get("staff_type") == "Worker" and doc.get("is_team_leader") != 1:
		_update_single_worker_from_manager(doc)
	if doc.get("external_report_to"):
		doc.external_reporting_manager = 1
	if doc.get("reports_to") and doc.get("external_report_to"):
		frappe.throw(_("Employee cannot have both internal and external reporting manager. Please select only one."))


def update_worker_fields_from_manager(doc, method=None):
	"""
	Hook for Employee doctype on_update

	Case 2: If this is a Team Leader (is_team_leader=1)
	- Find all workers reporting to this team leader and update their fields.
	"""
	# Case 2: If this is a team leader, update all workers reporting to them
	if doc.get("is_team_leader") == 1:
		_update_workers_under_team_leader(doc)


def _update_single_worker_from_manager(doc):
	"""
	Update a single worker's fields from their reporting manager.
	Called during validate so changes are saved in the same transaction.
	"""
	# Check if external reporting manager
	if doc.get("external_reporting_manager") == 1:
		reporting_manager = doc.get("external_report_to")
		if not reporting_manager:
			_clear_worker_fields(doc)
			return

		external_manager_details = frappe.get_doc("Employee Pull", reporting_manager)
		doc.external_sales_order = 1
		doc.external_order = external_manager_details.get("sales_order") + "-" + external_manager_details.get("company")
		doc.external_business_vertical = external_manager_details.get("business_line")
		doc.external_so = external_manager_details.get("sales_order")
		doc.sales_order = None
		doc.business_vertical = None
		return

	# Check if reports_to field exists and has a value
	reporting_manager = doc.get("reports_to")
	if not reporting_manager:
		_clear_worker_fields(doc)
		return

	# Get the reporting manager's details
	manager_data = frappe.db.get_value(
		"Employee",
		reporting_manager,
		[
			"business_vertical",
			"sales_order",
			"external_sales_order",
			"external_order",
			"external_business_vertical",
			"external_so"
		],
		as_dict=True
	)

	if not manager_data:
		frappe.throw(_("Could not fetch manager data for Employee: {0}").format(reporting_manager))

	# Check if manager has external sales order
	if manager_data.get("external_sales_order") == 1:
		doc.external_sales_order = 1
		doc.external_order = manager_data.get("external_order")
		doc.external_business_vertical = manager_data.get("external_business_vertical")
		doc.external_so = manager_data.get("external_so")
		doc.business_vertical = None
		doc.sales_order = None
	else:
		doc.external_sales_order = 0
		doc.business_vertical = manager_data.get("business_vertical")
		doc.sales_order = manager_data.get("sales_order")
		doc.external_order = None
		doc.external_business_vertical = None
		doc.external_so = None


def _clear_worker_fields(doc):
	"""Clear all worker fields related to manager"""
	doc.business_vertical = None
	doc.sales_order = None
	doc.external_sales_order = 0
	doc.external_order = None
	doc.external_business_vertical = None
	doc.external_so = None


def _update_workers_under_team_leader(team_leader_doc):
	"""
	When a team leader's information changes, update all workers reporting to them.
	"""
	# Find all workers reporting to this team leader
	workers = frappe.get_all(
		"Employee",
		filters={
			"reports_to": team_leader_doc.name,
			"staff_type": "Worker",
			"is_team_leader": 0,
			"status": "Active"
		},
		fields=["name"]
	)

	if not workers:
		return

	# Prepare manager data
	manager_data = {
		"business_vertical": team_leader_doc.get("business_vertical"),
		"sales_order": team_leader_doc.get("sales_order"),
		"external_sales_order": team_leader_doc.get("external_sales_order"),
		"external_order": team_leader_doc.get("external_order"),
		"external_business_vertical": team_leader_doc.get("external_business_vertical"),
		"external_so": team_leader_doc.get("external_so")
	}

	# Update each worker
	for worker in workers:
		worker_doc = frappe.get_doc("Employee", worker.name)

		if manager_data.get("external_sales_order") == 1:
			worker_doc.external_sales_order = 1
			worker_doc.external_order = manager_data.get("external_order")
			worker_doc.external_business_vertical = manager_data.get("external_business_vertical")
			worker_doc.external_so = manager_data.get("external_so")
			worker_doc.business_vertical = None
			worker_doc.sales_order = None
		else:
			worker_doc.external_sales_order = 0
			worker_doc.business_vertical = manager_data.get("business_vertical")
			worker_doc.sales_order = manager_data.get("sales_order")
			worker_doc.external_order = None
			worker_doc.external_business_vertical = None
			worker_doc.external_so = None

		# Save without triggering hooks to avoid recursion
		worker_doc.flags.ignore_validate_update_after_submit = True
		worker_doc.save(ignore_permissions=True)

	frappe.msgprint(
		_("Updated {0} worker(s) with your changed information").format(len(workers)),
		alert=True,
		indicator="green"
	)


def update_workers_from_employee_pull(doc, method=None):
	"""
	Hook for Employee Pull doctype on_update
	When an Employee Pull record is updated, find all workers who reference it
	as their external reporting manager and update them.
	"""
	# Find all workers who have this Employee Pull as their external reporting manager
	workers = frappe.get_all(
		"Employee",
		filters={
			"external_report_to": doc.name,
			"external_reporting_manager": 1,
			"staff_type": "Worker",
			"is_team_leader": 0,
			"status": "Active"
		},
		fields=["name"]
	)

	if not workers:
		return

	# Prepare Employee Pull data
	employee_pull_data = {
		"sales_order": doc.get("sales_order"),
		"business_line": doc.get("business_line"),
		"company": doc.get("company")
	}

	# Update each worker
	for worker in workers:
		worker_doc = frappe.get_doc("Employee", worker.name)

		worker_doc.external_sales_order = 1
		worker_doc.external_order = employee_pull_data.get("sales_order") + "-" + employee_pull_data.get("company")
		worker_doc.external_business_vertical = employee_pull_data.get("business_line")
		worker_doc.external_so = employee_pull_data.get("sales_order")
		worker_doc.business_vertical = None
		worker_doc.sales_order = None

		# Save without triggering hooks to avoid recursion
		worker_doc.flags.ignore_validate_update_after_submit = True
		worker_doc.save(ignore_permissions=True)

	frappe.msgprint(
		_("Updated {0} worker(s) referencing this Employee Pull record").format(len(workers)),
		alert=True,
		indicator="green"
	)
