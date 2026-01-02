# -*- coding: utf-8 -*-
# Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
import json
import requests
from frappe import _
from frappe.utils import now, get_datetime


# ==================== WHITELISTED APIs FOR RECEIVING SYNC DATA ====================

@frappe.whitelist()
def receive_employee_pull(data, source_site=None):
	"""
	API endpoint to receive Employee Pull data from remote ERP
	"""
	try:
		if isinstance(data, str):
			data = json.loads(data)
		
		# Check if Employee Pull already exists
		name = data.get("employee") + "-" + data.get("company")
		existing = frappe.db.exists("Employee Pull", name)
		
		if existing:
			# Update existing
			doc = frappe.get_doc("Employee Pull", name)
			doc.employee = data.get("employee")
			doc.employee_name = data.get("employee_name")
			doc.sales_order = data.get("sales_order")
			doc.business_line = data.get("business_line")
			doc.company = data.get("company")
			doc.flags.ignore_sync = True  # Prevent re-syncing back
			doc.save(ignore_permissions=True)
		else:
			# Create new
			doc = frappe.get_doc({
				"doctype": "Employee Pull",
				"employee": data.get("employee"),
				"employee_name": data.get("employee_name"),
				"sales_order": data.get("sales_order"),
				"business_line": data.get("business_line"),
				"company": data.get("company")
			})
			doc.flags.ignore_sync = True  # Prevent re-syncing back
			doc.insert(ignore_permissions=True)
		
		frappe.db.commit()
		return {"success": True, "message": "Employee Pull synced successfully"}
		
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error receiving Employee Pull data"
		)
		return {"success": False, "message": str(e)}


@frappe.whitelist()
def receive_sales_order_pull(data, source_site=None):
	"""
	API endpoint to receive Sales Order Pull data from remote ERP
	"""
	try:
		if isinstance(data, str):
			data = json.loads(data)
		
		name = data.get("sales_order") + "-" + data.get("company")
		# Check if Sales Order Pull already exists
		existing = frappe.db.exists("Sales Order Pull", name)
		
		if existing:
			# Update existing
			doc = frappe.get_doc("Sales Order Pull", name)
			doc.sales_order = data.get("sales_order")
			doc.business_line = data.get("business_line")
			doc.company = data.get("company")
			doc.flags.ignore_sync = True  # Prevent re-syncing back
			doc.save(ignore_permissions=True)
		else:
			# Create new
			doc = frappe.get_doc({
				"doctype": "Sales Order Pull",
				"sales_order": data.get("sales_order"),
				"business_line": data.get("business_line"),
				"company": data.get("company")
			})
			doc.flags.ignore_sync = True  # Prevent re-syncing back
			doc.insert(ignore_permissions=True)
		
		frappe.db.commit()
		return {"success": True, "message": "Sales Order Pull synced successfully"}
		
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error receiving Sales Order Pull data"
		)
		return {"success": False, "message": str(e)}


@frappe.whitelist()
def receive_leader_location(data, source_site=None):
	"""
	API endpoint to receive Leader Location data from remote ERP
	"""
	try:
		if isinstance(data, str):
			data = json.loads(data)
		
		# Find Employee Pull record using employee and company
		employee_id = data.get("employee")
		company = data.get("company")
		
		if not employee_id or not company:
			return {"success": False, "message": "Employee and Company are required"}
		
		# Get Employee Pull record
		employee_pull = frappe.db.get_value(
			"Employee Pull",
			{"employee": employee_id, "company": company},
			"name"
		)
		
		if not employee_pull:
			frappe.log_error(
				message="No Employee Pull found for employee {0}, company {1}".format(employee_id, company),
				title="Leader Location Sync - Employee Pull Not Found"
			)
			return {"success": False, "message": "Employee Pull not found for employee {0}".format(employee_id)}
		
		# Create new Leader Location
		doc = frappe.get_doc({
			"doctype": "Leader Location",
			"employee": employee_pull,  # Use Employee Pull name
			"datetime": data.get("datetime"),
			"location": data.get("location")
		})
		doc.flags.ignore_sync = True  # Prevent re-syncing back
		doc.insert(ignore_permissions=True)
		
		frappe.db.commit()
		return {"success": True, "message": "Leader Location synced successfully"}
		
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error receiving Leader Location data"
		)
		return {"success": False, "message": str(e)}


@frappe.whitelist()
def receive_leave_pull(data, source_site=None):
	"""
	API endpoint to receive Leave Pull data from remote ERP
	"""
	try:
		if isinstance(data, str):
			data = json.loads(data)
		
		# Check if Leave Pull already exists
		leave_id = data.get("leave_id")
		existing = frappe.db.exists("Leave Pull", leave_id)
		
		if existing:
			# Update existing
			doc = frappe.get_doc("Leave Pull", leave_id)
			doc.employee = data.get("employee")
			doc.employee_name = data.get("employee_name")
			doc.from_date = data.get("from_date")
			doc.to_date = data.get("to_date")
			doc.total_no_of_days = data.get("total_no_of_days")
			doc.half_day = data.get("half_day", 0)
			doc.half_day_date = data.get("half_day_date")
			doc.status = data.get("status")
			doc.approver = data.get("approver")
			doc.alternate_mobile_no = data.get("alternate_mobile_no")
			doc.reason = data.get("reason")
			doc.approved_from_date = data.get("approved_from_date")
			doc.approved_to_date = data.get("approved_to_date")
			doc.total_no_of_approved_days = data.get("total_no_of_approved_days")
			doc.source_erp = source_site
			doc.flags.ignore_sync = True  # Prevent re-syncing back
			doc.save(ignore_permissions=True)
		else:
			# Create new
			doc = frappe.get_doc({
				"doctype": "Leave Pull",
				"leave_id": leave_id,
				"employee": data.get("employee"),
				"employee_name": data.get("employee_name"),
				"from_date": data.get("from_date"),
				"to_date": data.get("to_date"),
				"total_no_of_days": data.get("total_no_of_days"),
				"half_day": data.get("half_day", 0),
				"half_day_date": data.get("half_day_date"),
				"status": data.get("status"),
				"approver": data.get("approver"),
				"alternate_mobile_no": data.get("alternate_mobile_no"),
				"reason": data.get("reason"),
				"approved_from_date": data.get("approved_from_date"),
				"approved_to_date": data.get("approved_to_date"),
				"total_no_of_approved_days": data.get("total_no_of_approved_days"),
				"source_erp": source_site
			})
			doc.flags.ignore_sync = True  # Prevent re-syncing back
			doc.insert(ignore_permissions=True)
		
		frappe.db.commit()
		return {"success": True, "message": "Leave Pull synced successfully"}
		
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error receiving Leave Pull data"
		)
		return {"success": False, "message": str(e)}


@frappe.whitelist()
def receive_expense_pull(data, source_site=None):
	"""
	API endpoint to receive Expense Pull data from remote ERP
	"""
	try:
		if isinstance(data, str):
			data = json.loads(data)
		
		# Check if Expense Pull already exists
		expense_id = data.get("expense_id")
		existing = frappe.db.exists("Expense Pull", expense_id)
		
		if existing:
			# Update existing
			doc = frappe.get_doc("Expense Pull", expense_id)
			doc.sent_by = data.get("sent_by")
			doc.employee_name = data.get("employee_name")
			doc.date_of_entry = data.get("date_of_entry")
			doc.date_of_expense = data.get("date_of_expense")
			doc.amount = data.get("amount")
			doc.details_of_expense = data.get("details_of_expense")
			doc.invoice_upload = data.get("invoice_upload")
			doc.sales_order = data.get("sales_order")
			doc.amount_approved = data.get("amount_approved")
			doc.purpose = data.get("purpose")
			doc.query = data.get("query")
			doc.status = data.get("status")
			doc.expense_type = data.get("expense_type")
			doc.approval_manager = data.get("approval_manager")
			doc.business_line = data.get("business_line")
			doc.expense_claim_type = data.get("expense_claim_type")
			doc.approved_by_manager = data.get("approved_by_manager", 0)
			doc.source_erp = source_site
			doc.flags.ignore_sync = True  # Prevent re-syncing back
			doc.save(ignore_permissions=True)
		else:
			# Create new
			doc = frappe.get_doc({
				"doctype": "Expense Pull",
				"expense_id": expense_id,
				"sent_by": data.get("sent_by"),
				"employee_name": data.get("employee_name"),
				"date_of_entry": data.get("date_of_entry"),
				"date_of_expense": data.get("date_of_expense"),
				"amount": data.get("amount"),
				"details_of_expense": data.get("details_of_expense"),
				"invoice_upload": data.get("invoice_upload"),
				"sales_order": data.get("sales_order"),
				"amount_approved": data.get("amount_approved"),
				"purpose": data.get("purpose"),
				"query": data.get("query"),
				"status": data.get("status"),
				"expense_type": data.get("expense_type"),
				"approval_manager": data.get("approval_manager"),
				"business_line": data.get("business_line"),
				"expense_claim_type": data.get("expense_claim_type"),
				"approved_by_manager": data.get("approved_by_manager", 0),
				"source_erp": source_site
			})
			doc.flags.ignore_sync = True  # Prevent re-syncing back
			doc.insert(ignore_permissions=True)
		
		frappe.db.commit()
		return {"success": True, "message": "Expense Pull synced successfully"}
		
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error receiving Expense Pull data"
		)
		return {"success": False, "message": str(e)}


@frappe.whitelist()
def receive_leave_status_update(data, source_site=None):
	"""
	API endpoint to receive status updates for OTPL Leave from remote ERP
	Updates the original OTPL Leave record when status changes in Leave Pull
	"""
	try:
		if isinstance(data, str):
			data = json.loads(data)
		
		leave_id = data.get("leave_id")
		status = data.get("status")
		
		if not leave_id or not status:
			return {"success": False, "message": "leave_id and status are required"}
		
		# Check if OTPL Leave exists
		if not frappe.db.exists("OTPL Leave", leave_id):
			return {"success": False, "message": "OTPL Leave {0} not found".format(leave_id)}
		
		# Update the OTPL Leave status
		doc = frappe.get_doc("OTPL Leave", leave_id)
		doc.status = status
		
		# Update approved dates if provided
		if data.get("approved_from_date"):
			doc.approved_from_date = data.get("approved_from_date")
		if data.get("approved_to_date"):
			doc.approved_to_date = data.get("approved_to_date")
		if data.get("total_no_of_approved_days"):
			doc.total_no_of_approved_days = data.get("total_no_of_approved_days")
		
		doc.flags.ignore_sync = True  # Prevent re-syncing back
		doc.save(ignore_permissions=True)
		
		frappe.db.commit()
		return {"success": True, "message": "Leave status updated successfully"}
		
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error receiving Leave status update"
		)
		return {"success": False, "message": str(e)}


@frappe.whitelist()
def receive_expense_status_update(data, source_site=None):
	"""
	API endpoint to receive approval updates for OTPL Expense from remote ERP
	Updates the original OTPL Expense record when approved in Expense Pull
	OTPL Expense uses checkbox (approved_by_manager) for approval management
	"""
	try:
		if isinstance(data, str):
			data = json.loads(data)
		
		expense_id = data.get("expense_id")
		
		if not expense_id:
			return {"success": False, "message": "expense_id is required"}
		
		# Check if OTPL Expense exists
		if not frappe.db.exists("OTPL Expense", expense_id):
			return {"success": False, "message": "OTPL Expense {0} not found".format(expense_id)}
		
		# Update the OTPL Expense approval status
		doc = frappe.get_doc("OTPL Expense", expense_id)
		
		# Primary field: approved_by_manager checkbox
		if data.get("approved_by_manager") is not None:
			doc.approved_by_manager = data.get("approved_by_manager")
		
		# Update amount_approved if provided
		if data.get("amount_approved"):
			doc.amount_approved = data.get("amount_approved")
		
		# Update status if provided (optional field)
		if data.get("status"):
			doc.status = data.get("status")
		
		doc.flags.ignore_sync = True  # Prevent re-syncing back
		doc.save(ignore_permissions=True)
		
		frappe.db.commit()
		return {"success": True, "message": "Expense approval status updated successfully"}
		
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error receiving Expense approval update"
		)
		return {"success": False, "message": str(e)}


@frappe.whitelist()
def get_employees_for_sync(filters=None):
	"""
	API endpoint to get all team leader employees for initial pull
	Uses metadata to dynamically get all fields
	"""
	try:
		field_names = ["name", "employee_name", "company","sales_order","business_vertical","external_sales_order","external_order","external_business_vertical","external_so"]
		

		# Get all team leader employees from Employee doctype
		employees = frappe.get_all(
			"Employee",
			filters={"is_team_leader": 1, "status": "Active"},
			fields=field_names,
			limit_page_length=None
		)
		
		# Transform to Employee Pull format
		employee_data = []
		for emp in employees:
			employee_data.append({
				"employee": emp.get("name"),
				"employee_name": emp.get("employee_name"),
				"sales_order": emp.get("sales_order") if not emp.get("external_sales_order") == 1 else emp.get("external_so"),
				"business_line": emp.get("business_vertical") if not emp.get("external_sales_order") == 1 else emp.get("external_business_vertical"),
				"company": emp.get("company")
			})
		
		return {"success": True, "data": employee_data}
		
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error getting employees for sync"
		)
		return {"success": False, "message": str(e)}


@frappe.whitelist()
def get_sales_orders_for_sync(filters=None):
	"""
	API endpoint to get all sales orders for initial pull
	Uses metadata to dynamically get all fields
	"""
	try:
		field_names = ["name", "company","business_line"]
		
		
		# Get all Sales Order records
		sales_orders = frappe.get_all(
			"Sales Order",
			filters={},  # Only submitted sales orders
			fields=field_names,
			limit_page_length=None
		)
		
		# Transform to Sales Order Pull format
		sales_order_data = []
		for so in sales_orders:
			sales_order_data.append({
				"sales_order": so.get("name"),
				"business_line": so.get("business_line"),
				"company": so.get("company")
			})
		
		return {"success": True, "data": sales_order_data}
		
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error getting sales orders for sync"
		)
		return {"success": False, "message": str(e)}


# ==================== INITIAL PULL FUNCTIONS ====================

@frappe.whitelist()
def initial_pull_from_remote_erp(erp_sync_settings):
	"""
	Pull all employees and sales orders from remote ERP for initial setup
	Called from ERP Sync Settings button
	"""
	try:
		settings = frappe.get_doc("ERP Sync Settings", erp_sync_settings)
		
		if not settings.enabled:
			return {"success": False, "message": "ERP Sync Settings is not enabled"}
		
		results = {
			"employees_pulled": 0,
			"sales_orders_pulled": 0,
			"errors": []
		}
		
		# Pull Sales Orders
		if settings.sync_sales_order_pull:
			so_result = pull_sales_orders_from_remote(settings)
			results["sales_orders_pulled"] = so_result.get("count", 0)
			if so_result.get("error"):
				results["errors"].append(so_result.get("error"))
		
		# Pull Employees
		if settings.sync_employee:
			emp_result = pull_employees_from_remote(settings)
			results["employees_pulled"] = emp_result.get("count", 0)
			if emp_result.get("error"):
				results["errors"].append(emp_result.get("error"))
		
		# Update last pull time
		settings.last_pull_time = now()
		settings.save(ignore_permissions=True)
		frappe.db.commit()
		
		return {"success": True, "data": results}
		
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error in initial pull from remote ERP"
		)
		return {"success": False, "message": str(e)}


def pull_employees_from_remote(settings):
	"""Pull all employees from remote ERP"""
	try:
		url = "{0}/api/method/employee_self_service.employee_self_service.utils.erp_sync.get_employees_for_sync".format(
			settings.erp_url
		)
		
		headers = {
			"Authorization": "token {0}:{1}".format(
				settings.get_password("api_key"),
				settings.get_password("api_secret")
			),
			"Content-Type": "application/json"
		}
		
		response = requests.get(url, headers=headers, timeout=60)
		
		if response.status_code == 200:
			data = response.json()
			if data.get("message", {}).get("success"):
				employees = data.get("message", {}).get("data", [])
				count = 0
				
				for emp in employees:
					try:
						# Check if already exists by employee and company
						existing = frappe.db.get_value(
							"Employee Pull",
							{"employee": emp.get("employee"), "company": emp.get("company")},
							"name"
						)
						
						if existing:
							# Update existing record
							doc = frappe.get_doc("Employee Pull", existing)
							doc.employee_name = emp.get("employee_name")
							doc.sales_order = emp.get("sales_order")
							doc.business_line = emp.get("business_line")
							doc.flags.ignore_sync = True
							doc.save(ignore_permissions=True)
						else:
							# Create new record
							doc = frappe.get_doc({
								"doctype": "Employee Pull",
								"employee": emp.get("employee"),
								"employee_name": emp.get("employee_name"),
								"sales_order": emp.get("sales_order"),
								"business_line": emp.get("business_line"),
								"company": emp.get("company")
							})
							doc.flags.ignore_sync = True
							doc.insert(ignore_permissions=True)
						count += 1
					except Exception as e:
						frappe.log_error(
							message="Error creating Employee Pull: {0}".format(str(e)),
							title="Employee Pull Creation Error"
						)
				
				frappe.db.commit()
				return {"count": count}
			else:
				return {"count": 0, "error": data.get("message", {}).get("message")}
		else:
			return {"count": 0, "error": "HTTP {0}".format(response.status_code)}
			
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error pulling employees from remote"
		)
		return {"count": 0, "error": str(e)}


def pull_sales_orders_from_remote(settings):
	"""Pull all sales orders from remote ERP"""
	try:
		url = "{0}/api/method/employee_self_service.employee_self_service.utils.erp_sync.get_sales_orders_for_sync".format(
			settings.erp_url
		)
		
		headers = {
			"Authorization": "token {0}:{1}".format(
				settings.get_password("api_key"),
				settings.get_password("api_secret")
			),
			"Content-Type": "application/json"
		}
		
		response = requests.get(url, headers=headers, timeout=60)
		
		if response.status_code == 200:
			data = response.json()
			if data.get("message", {}).get("success"):
				sales_orders = data.get("message", {}).get("data", [])
				count = 0
				
				for so in sales_orders:
					try:
						# Check if already exists by sales_order and company
						existing = frappe.db.get_value(
							"Sales Order Pull",
							{"sales_order": so.get("sales_order"), "company": so.get("company")},
							"name"
						)
						
						if existing:
							# Update existing record
							doc = frappe.get_doc("Sales Order Pull", existing)
							doc.business_line = so.get("business_line")
							doc.flags.ignore_sync = True
							doc.save(ignore_permissions=True)
						else:
							# Create new record
							doc = frappe.get_doc({
								"doctype": "Sales Order Pull",
								"sales_order": so.get("sales_order"),
								"business_line": so.get("business_line"),
								"company": so.get("company")
							})
							doc.flags.ignore_sync = True
							doc.insert(ignore_permissions=True)
						count += 1
					except Exception as e:
						frappe.log_error(
							message="Error creating Sales Order Pull: {0}".format(str(e)),
							title="Sales Order Pull Creation Error"
						)
				
				frappe.db.commit()
				return {"count": count}
			else:
				return {"count": 0, "error": data.get("message", {}).get("message")}
		else:
			return {"count": 0, "error": "HTTP {0}".format(response.status_code)}
			
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error pulling sales orders from remote"
		)
		return {"count": 0, "error": str(e)}


# ==================== SYNC QUEUE FUNCTIONS ====================

def queue_sync_request(doctype_name, document_name, sync_action="Create/Update"):
	"""
	Queue a document sync request to all enabled ERP Sync Settings
	This function should be called from document hooks
	"""
	try:
		# Get all enabled ERP Sync Settings
		sync_settings = frappe.get_all(
			"ERP Sync Settings",
			filters={"enabled": 1},
			fields=["name", "sync_employee", "sync_sales_order_pull", "sync_leader_location"]
		)
		
		if not sync_settings:
			return
		
		# Get the document data
		doc = frappe.get_doc(doctype_name, document_name)
		sync_data = get_sync_data(doc)
		
		# Queue sync request for each enabled ERP
		for settings in sync_settings:
			# Check if this doctype should be synced based on settings
			should_sync = False
			if doctype_name == "Employee Pull" and settings.get("sync_employee"):
				should_sync = True
			elif doctype_name == "Sales Order Pull" and settings.get("sync_sales_order_pull"):
				should_sync = True
			elif doctype_name == "Leader Location" and settings.get("sync_leader_location"):
				should_sync = True
			
			if should_sync:
				# Create queue entry
				queue_doc = frappe.get_doc({
					"doctype": "ERP Sync Queue",
					"erp_sync_settings": settings.name,
					"doctype_name": doctype_name,
					"document_name": document_name,
					"sync_action": sync_action,
					"status": "Pending",
					"retry_count": 0,
					"sync_data": json.dumps(sync_data, default=str)
				})
				queue_doc.insert(ignore_permissions=True)
				frappe.db.commit()
				
				# Enqueue the sync job
				frappe.enqueue(
					"employee_self_service.employee_self_service.utils.erp_sync.process_sync_queue_item",
					queue="default",
					timeout=300,
					queue_name=queue_doc.name,
					is_async=True,
					now=False
				)
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error queuing sync request for {0} - {1}".format(doctype_name, document_name)
		)


def get_sync_data(doc):
	"""
	Extract sync data from document based on doctype
	"""
	sync_data = {}
	
	if doc.doctype == "Employee Pull":
		# Sync employee data for team leaders
		sync_data = {
			"name": doc.name,
			"employee": doc.get("employee"),
			"employee_name": doc.get("employee_name"),
			"sales_order": doc.get("sales_order"),
			"business_line": doc.get("business_line"),
			"company": doc.get("company")
		}
	
	elif doc.doctype == "Sales Order Pull":
		# Sync sales order pull data
		sync_data = {
			"doctype": "Sales Order Pull",
			"sales_order": doc.get("sales_order"),
			"business_line": doc.get("business_line"),
			"company": doc.get("company")
		}
	
	elif doc.doctype == "Leader Location":
		# Sync leader location data
		# Get employee and company from Employee Pull
		employee_pull = frappe.get_doc("Employee Pull", doc.get("employee"))
		sync_data = {
			"employee": employee_pull.get("employee"),  # Send actual Employee ID
			"company": employee_pull.get("company"),
			"datetime": doc.get("datetime"),
			"location": doc.get("location")
		}
	
	return sync_data


def process_sync_queue_item(queue_name):
	"""
	Process a single sync queue item
	This function is called via background job
	"""
	try:
		queue_doc = frappe.get_doc("ERP Sync Queue", queue_name)
		
		# Check if already completed
		if queue_doc.status == "Completed":
			return
		
		# Update status to Processing
		queue_doc.status = "Processing"
		queue_doc.last_attempt_time = now()
		queue_doc.save(ignore_permissions=True)
		frappe.db.commit()
		
		# Get sync settings
		sync_settings = frappe.get_doc("ERP Sync Settings", queue_doc.erp_sync_settings)
		
		if not sync_settings.enabled:
			queue_doc.status = "Failed"
			queue_doc.error_log = "ERP Sync Settings is disabled"
			queue_doc.save(ignore_permissions=True)
			frappe.db.commit()
			return
		
		# Prepare sync data
		sync_data = json.loads(queue_doc.sync_data)
		
		# Send data to remote ERP
		success = send_to_remote_erp(
			sync_settings.erp_url,
			sync_settings.get_password("api_key"),
			sync_settings.get_password("api_secret"),
			queue_doc.doctype_name,
			sync_data,
			queue_doc.sync_action
		)
		
		if success:
			queue_doc.status = "Completed"
			queue_doc.error_log = ""
		else:
			raise Exception("Failed to sync with remote ERP")
		
		queue_doc.save(ignore_permissions=True)
		frappe.db.commit()
		
	except Exception as e:
		handle_sync_error(queue_name, str(e))


def send_to_remote_erp(erp_url, api_key, api_secret, doctype_name, data, sync_action):
	"""
	Send data to remote ERP via custom whitelisted API
	"""
	try:
		# Prepare headers
		headers = {
			"Authorization": "token {0}:{1}".format(api_key, api_secret),
			"Content-Type": "application/json"
		}
		
		# Determine the API endpoint based on doctype
		api_method = ""
		if doctype_name == "Employee Pull":
			api_method = "receive_employee_pull"
		elif doctype_name == "Sales Order Pull":
			api_method = "receive_sales_order_pull"
		elif doctype_name == "Leader Location":
			api_method = "receive_leader_location"
		elif doctype_name == "Leave Pull":
			api_method = "receive_leave_pull"
		elif doctype_name == "Expense Pull":
			api_method = "receive_expense_pull"
		elif doctype_name == "Leave Status Update":
			api_method = "receive_leave_status_update"
		elif doctype_name == "Expense Status Update":
			api_method = "receive_expense_status_update"
		else:
			return False
		
		url = "{0}/api/method/employee_self_service.employee_self_service.utils.erp_sync.{1}".format(
			erp_url, api_method
		)
		
		# Send data to remote ERP
		payload = {
			"data": json.dumps(data)
		}
		
		response = requests.post(url, headers=headers, json=payload, timeout=30)
		
		# Check response
		if response.status_code == 200:
			response_data = response.json()
			if response_data.get("message", {}).get("success"):
				return True
			else:
				frappe.log_error(
					message="Response: {0}".format(response.text),
					title="Remote ERP API Error - {0}".format(api_method)
				)
				return False
		else:
			frappe.log_error(
				message="Status Code: {0}\nResponse: {1}".format(response.status_code, response.text),
				title="Remote ERP API Error"
			)
			return False
			
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error sending data to remote ERP"
		)
		return False


def handle_sync_error(queue_name, error_message):
	"""
	Handle sync errors and retry logic
	"""
	try:
		queue_doc = frappe.get_doc("ERP Sync Queue", queue_name)
		queue_doc.retry_count += 1
		queue_doc.error_log = error_message
		queue_doc.last_attempt_time = now()
		
		if queue_doc.retry_count < queue_doc.max_retries:
			# Retry
			queue_doc.status = "Pending"
			queue_doc.save(ignore_permissions=True)
			frappe.db.commit()
			
			# Enqueue retry with delay
			frappe.enqueue(
				"employee_self_service.employee_self_service.utils.erp_sync.process_sync_queue_item",
				queue="default",
				timeout=300,
				queue_name=queue_doc.name,
				is_async=True,
				now=False
			)
		else:
			# Max retries reached
			queue_doc.status = "Failed"
			queue_doc.save(ignore_permissions=True)
			frappe.db.commit()
			
			frappe.log_error(
				message="Max retries reached. Error: {0}".format(error_message),
				title="Sync Failed for {0}".format(queue_name)
			)
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error handling sync error for {0}".format(queue_name)
		)


@frappe.whitelist()
def retry_sync_queue_item(queue_name):
	"""
	Manually retry a failed sync queue item
	"""
	queue_doc = frappe.get_doc("ERP Sync Queue", queue_name)
	
	if queue_doc.status == "Failed" and queue_doc.retry_count < queue_doc.max_retries:
		queue_doc.status = "Pending"
		queue_doc.save(ignore_permissions=True)
		frappe.db.commit()
		
		# Enqueue the sync job
		frappe.enqueue(
			"employee_self_service.employee_self_service.utils.erp_sync.process_sync_queue_item",
			queue="default",
			timeout=300,
			queue_name=queue_doc.name,
			is_async=True,
			now=False
		)
		
		return True
	return False


def process_pending_sync_queue():
	"""
	Process all pending sync queue items
	This function is called by scheduler
	"""
	try:
		# Get all pending queue items where retry_count < max_retries
		# Using SQL for version 12 compatibility
		pending_items = frappe.db.sql("""
			SELECT name 
			FROM `tabERP Sync Queue`
			WHERE status = 'Pending'
			AND retry_count < max_retries
			LIMIT 100
		""", as_dict=1)
		
		for item in pending_items:
			# Enqueue each item
			frappe.enqueue(
				"employee_self_service.employee_self_service.utils.erp_sync.process_sync_queue_item",
				queue="default",
				timeout=300,
				queue_name=item.name,
				is_async=True,
				now=False
			)
		
		if pending_items:
			frappe.log("Queued {0} pending sync items for processing".format(len(pending_items)))
			
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error processing pending sync queue"
		)


# ==================== EMPLOYEE AND SALES ORDER SYNC HOOKS ====================

def sync_employee_to_remote(doc, method=None):
	"""
	Hook for Employee doctype on_update
	Only syncs if employee is team leader and relevant fields changed
	"""
	try:
		
		# Only sync team leaders
		if not doc.is_team_leader:
			frappe.log_error("Not a team leader: {0}".format(doc.name), "ERP Sync Debug")
			return
		
		# Prepare employee data
		employee_data = {
			"employee": doc.name,
			"employee_name": doc.employee_name,
			"sales_order": doc.get("sales_order") if not doc.get("external_sales_order") == 1 else doc.get("external_so"),
			"business_line": doc.get("business_vertical") if not doc.get("external_sales_order") == 1 else doc.get("external_business_vertical"),
			"company": doc.company
		}
		
		# Get all enabled ERP Sync Settings
		sync_settings = frappe.get_all(
			"ERP Sync Settings",
			filters={"enabled": 1, "sync_employee": 1},
			fields=["name"]
		)
		
		if not sync_settings:
			frappe.log_error("No enabled ERP Sync Settings for Employee sync", "ERP Sync Debug")
			return
		
		# Queue sync for each remote ERP
		for settings in sync_settings:
			# Create queue entry
			queue_doc = frappe.get_doc({
				"doctype": "ERP Sync Queue",
				"erp_sync_settings": settings.name,
				"doctype_name": "Employee",
				"document_name": doc.name,
				"sync_action": "Create/Update",
				"status": "Pending",
				"retry_count": 0,
				"sync_data": json.dumps(employee_data, default=str)
			})
			queue_doc.insert(ignore_permissions=True)
			frappe.db.commit()
			
			# Enqueue the sync job - immediate execution
			frappe.enqueue(
				"employee_self_service.employee_self_service.utils.erp_sync.process_sync_queue_item_employee",
				queue="default",
				timeout=300,
				queue_name=queue_doc.name,
				is_async=True,
				now=True  # Execute immediately
			)
			
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error syncing Employee {0} to remote".format(doc.name)
		)


def sync_sales_order_to_remote(doc, method=None):
	"""
	Hook for Sales Order doctype on_update
	Only syncs if relevant fields changed and sales order is submitted
	"""
	try:
		# Prepare sales order data
		sales_order_data = {
			"sales_order": doc.name,
			"business_line": doc.get("business_line"),
			"company": doc.company
		}
		
		# Get all enabled ERP Sync Settings
		sync_settings = frappe.get_all(
			"ERP Sync Settings",
			filters={"enabled": 1, "sync_sales_order_pull": 1},
			fields=["name"]
		)
		
		if not sync_settings:
			return
		
		# Queue sync for each remote ERP
		for settings in sync_settings:
			# Create queue entry
			queue_doc = frappe.get_doc({
				"doctype": "ERP Sync Queue",
				"erp_sync_settings": settings.name,
				"doctype_name": "Sales Order",
				"document_name": doc.name,
				"sync_action": "Create/Update",
				"status": "Pending",
				"retry_count": 0,
				"sync_data": json.dumps(sales_order_data, default=str)
			})
			queue_doc.insert(ignore_permissions=True)
			frappe.db.commit()
			
			# Enqueue the sync job - immediate execution
			frappe.enqueue(
				"employee_self_service.employee_self_service.utils.erp_sync.process_sync_queue_item_sales_order",
				queue="default",
				timeout=300,
				queue_name=queue_doc.name,
				is_async=True,
				now=True  # Execute immediately
			)
			
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error syncing Sales Order {0} to remote".format(doc.name)
		)


def process_sync_queue_item_employee(queue_name):
	"""
	Process Employee sync queue item - syncs to remote Employee Pull
	"""
	try:
		queue_doc = frappe.get_doc("ERP Sync Queue", queue_name)
		
		if queue_doc.status == "Completed":
			return
		
		# Update status to Processing
		queue_doc.status = "Processing"
		queue_doc.last_attempt_time = now()
		queue_doc.save(ignore_permissions=True)
		frappe.db.commit()
		
		# Get sync settings
		sync_settings = frappe.get_doc("ERP Sync Settings", queue_doc.erp_sync_settings)
		
		if not sync_settings.enabled:
			queue_doc.status = "Failed"
			queue_doc.error_log = "ERP Sync Settings is disabled"
			queue_doc.save(ignore_permissions=True)
			frappe.db.commit()
			return
		
		# Prepare sync data
		sync_data = json.loads(queue_doc.sync_data)
		
		# Send to remote ERP's receive_employee_pull endpoint
		success = send_to_remote_erp(
			sync_settings.erp_url,
			sync_settings.get_password("api_key"),
			sync_settings.get_password("api_secret"),
			"Employee Pull",
			sync_data,
			queue_doc.sync_action
		)
		
		if success:
			queue_doc.status = "Completed"
			queue_doc.error_log = ""
		else:
			raise Exception("Failed to sync Employee to remote ERP")
		
		queue_doc.save(ignore_permissions=True)
		frappe.db.commit()
		
	except Exception as e:
		handle_sync_error(queue_name, str(e))


def process_sync_queue_item_sales_order(queue_name):
	"""
	Process Sales Order sync queue item - syncs to remote Sales Order Pull
	"""
	try:
		queue_doc = frappe.get_doc("ERP Sync Queue", queue_name)
		
		if queue_doc.status == "Completed":
			return
		
		# Update status to Processing
		queue_doc.status = "Processing"
		queue_doc.last_attempt_time = now()
		queue_doc.save(ignore_permissions=True)
		frappe.db.commit()
		
		# Get sync settings
		sync_settings = frappe.get_doc("ERP Sync Settings", queue_doc.erp_sync_settings)
		
		if not sync_settings.enabled:
			queue_doc.status = "Failed"
			queue_doc.error_log = "ERP Sync Settings is disabled"
			queue_doc.save(ignore_permissions=True)
			frappe.db.commit()
			return
		
		# Prepare sync data
		sync_data = json.loads(queue_doc.sync_data)
		
		# Send to remote ERP's receive_sales_order_pull endpoint
		success = send_to_remote_erp(
			sync_settings.erp_url,
			sync_settings.get_password("api_key"),
			sync_settings.get_password("api_secret"),
			"Sales Order Pull",
			sync_data,
			queue_doc.sync_action
		)
		
		if success:
			queue_doc.status = "Completed"
			queue_doc.error_log = ""
		else:
			raise Exception("Failed to sync Sales Order to remote ERP")
		
		queue_doc.save(ignore_permissions=True)
		frappe.db.commit()
		
	except Exception as e:
		handle_sync_error(queue_name, str(e))


# ==================== LEAVE AND EXPENSE SYNC FUNCTIONS ====================

def push_leave_to_remote_erp(leave_doc):
	"""
	Push OTPL Leave data to all enabled remote ERPs when external manager is set
	Called from OTPL Leave on_update hook
	"""
	try:
		# Check if this is an external manager leave
		if not leave_doc.is_external_manager or not leave_doc.external_manager:
			return
		
		# Prevent infinite sync loop
		if hasattr(leave_doc, 'flags') and leave_doc.flags.get('ignore_sync'):
			return
		
		# Get all enabled ERP Sync Settings
		sync_settings_list = frappe.get_all(
			"ERP Sync Settings",
			filters={"enabled": 1},
			fields=["name"]
		)
		
		if not sync_settings_list:
			frappe.log_error("No enabled ERP Sync Settings for Leave sync", "Leave Sync Debug")
			return
		
		# Prepare leave data for sync
		leave_data = {
			"leave_id": leave_doc.name,
			"employee": leave_doc.employee,
			"employee_name": leave_doc.employee_name,
			"from_date": str(leave_doc.from_date) if leave_doc.from_date else None,
			"to_date": str(leave_doc.to_date) if leave_doc.to_date else None,
			"total_no_of_days": leave_doc.total_no_of_days,
			"half_day": leave_doc.half_day,
			"half_day_date": str(leave_doc.half_day_date) if leave_doc.half_day_date else None,
			"status": leave_doc.status,
			"approver": leave_doc.approver,
			"alternate_mobile_no": leave_doc.alternate_mobile_no,
			"reason": leave_doc.reason,
			"approved_from_date": str(leave_doc.approved_from_date) if leave_doc.approved_from_date else None,
			"approved_to_date": str(leave_doc.approved_to_date) if leave_doc.approved_to_date else None,
			"total_no_of_approved_days": leave_doc.total_no_of_approved_days
		}
		
		# Queue sync for each enabled ERP Sync Settings
		for settings in sync_settings_list:
			queue_doc = frappe.get_doc({
				"doctype": "ERP Sync Queue",
				"erp_sync_settings": settings.name,
				"sync_doctype": "OTPL Leave",
				"sync_docname": leave_doc.name,
				"sync_action": "Insert/Update",
				"sync_data": json.dumps(leave_data),
				"status": "Queued"
			})
			queue_doc.insert(ignore_permissions=True)
		
		frappe.db.commit()
		frappe.log_error(
			message="Queued Leave sync for {0} to {1} ERP(s)".format(leave_doc.name, len(sync_settings_list)),
			title="Leave Sync Queue Success"
		)
		
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error queueing Leave sync for {0}".format(leave_doc.name)
		)


def push_expense_to_remote_erp(expense_doc):
	"""
	Push OTPL Expense data to all enabled remote ERPs when external manager is set
	Called from OTPL Expense on_update hook
	"""
	try:
		# Check if this is an external manager expense
		if not expense_doc.is_external_manager or not expense_doc.external_manager:
			return
		
		# Prevent infinite sync loop
		if hasattr(expense_doc, 'flags') and expense_doc.flags.get('ignore_sync'):
			return
		
		# Get all enabled ERP Sync Settings
		sync_settings_list = frappe.get_all(
			"ERP Sync Settings",
			filters={"enabled": 1},
			fields=["name"]
		)
		
		if not sync_settings_list:
			frappe.log_error("No enabled ERP Sync Settings for Expense sync", "Expense Sync Debug")
			return
		
		# Prepare expense data for sync
		expense_data = {
			"expense_id": expense_doc.name,
			"sent_by": expense_doc.sent_by,
			"employee_name": expense_doc.employee_name,
			"date_of_entry": str(expense_doc.date_of_entry) if expense_doc.date_of_entry else None,
			"date_of_expense": str(expense_doc.date_of_expense) if expense_doc.date_of_expense else None,
			"amount": expense_doc.amount,
			"details_of_expense": expense_doc.details_of_expense,
			"invoice_upload": expense_doc.invoice_upload,
			"sales_order": expense_doc.sales_order,
			"amount_approved": expense_doc.amount_approved,
			"purpose": expense_doc.purpose,
			"query": expense_doc.query,
			"status": expense_doc.status,
			"expense_type": expense_doc.expense_type,
			"approval_manager": expense_doc.approval_manager,
			"business_line": expense_doc.business_line,
			"expense_claim_type": expense_doc.expense_claim_type,
			"approved_by_manager": expense_doc.approved_by_manager
		}
		
		# Queue sync for each enabled ERP Sync Settings
		for settings in sync_settings_list:
			queue_doc = frappe.get_doc({
				"doctype": "ERP Sync Queue",
				"erp_sync_settings": settings.name,
				"sync_doctype": "OTPL Expense",
				"sync_docname": expense_doc.name,
				"sync_action": "Insert/Update",
				"sync_data": json.dumps(expense_data),
				"status": "Queued"
			})
			queue_doc.insert(ignore_permissions=True)
		
		frappe.db.commit()
		frappe.log_error(
			message="Queued Expense sync for {0} to {1} ERP(s)".format(expense_doc.name, len(sync_settings_list)),
			title="Expense Sync Queue Success"
		)
		
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error queueing Expense sync for {0}".format(expense_doc.name)
		)


def process_leave_sync_queue(queue_name):
	"""
	Process queued Leave sync from ERP Sync Queue
	"""
	try:
		queue_doc = frappe.get_doc("ERP Sync Queue", queue_name)
		queue_doc.status = "Processing"
		queue_doc.save(ignore_permissions=True)
		frappe.db.commit()
		
		# Get sync settings
		sync_settings = frappe.get_doc("ERP Sync Settings", queue_doc.erp_sync_settings)
		
		if not sync_settings.enabled:
			queue_doc.status = "Failed"
			queue_doc.error_log = "ERP Sync Settings is disabled"
			queue_doc.save(ignore_permissions=True)
			frappe.db.commit()
			return
		
		# Parse sync data
		sync_data = json.loads(queue_doc.sync_data)
		
		# Send to remote ERP's receive_leave_pull endpoint
		success = send_to_remote_erp(
			sync_settings.erp_url,
			sync_settings.get_password("api_key"),
			sync_settings.get_password("api_secret"),
			"Leave Pull",
			sync_data,
			queue_doc.sync_action
		)
		
		if success:
			queue_doc.status = "Completed"
			queue_doc.error_log = ""
		else:
			raise Exception("Failed to sync Leave to remote ERP")
		
		queue_doc.save(ignore_permissions=True)
		frappe.db.commit()
		
	except Exception as e:
		handle_sync_error(queue_name, str(e))


def process_expense_sync_queue(queue_name):
	"""
	Process queued Expense sync from ERP Sync Queue
	"""
	try:
		queue_doc = frappe.get_doc("ERP Sync Queue", queue_name)
		queue_doc.status = "Processing"
		queue_doc.save(ignore_permissions=True)
		frappe.db.commit()
		
		# Get sync settings
		sync_settings = frappe.get_doc("ERP Sync Settings", queue_doc.erp_sync_settings)
		
		if not sync_settings.enabled:
			queue_doc.status = "Failed"
			queue_doc.error_log = "ERP Sync Settings is disabled"
			queue_doc.save(ignore_permissions=True)
			frappe.db.commit()
			return
		
		# Parse sync data
		sync_data = json.loads(queue_doc.sync_data)
		
		# Send to remote ERP's receive_expense_pull endpoint
		success = send_to_remote_erp(
			sync_settings.erp_url,
			sync_settings.get_password("api_key"),
			sync_settings.get_password("api_secret"),
			"Expense Pull",
			sync_data,
			queue_doc.sync_action
		)
		
		if success:
			queue_doc.status = "Completed"
			queue_doc.error_log = ""
		else:
			raise Exception("Failed to sync Expense to remote ERP")
		
		queue_doc.save(ignore_permissions=True)
		frappe.db.commit()
		
	except Exception as e:
		handle_sync_error(queue_name, str(e))


def push_leave_status_to_source(leave_pull_doc):
	"""
	Push Leave Pull status update back to source ERP's OTPL Leave
	Called from Leave Pull on_update hook
	"""
	try:
		# Check if this record came from an external source
		if not leave_pull_doc.source_erp:
			return
		
		# Prevent infinite sync loop
		if hasattr(leave_pull_doc, 'flags') and leave_pull_doc.flags.get('ignore_sync'):
			return
		
		# Find the ERP Sync Settings for the source ERP
		sync_settings = frappe.get_all(
			"ERP Sync Settings",
			filters={"enabled": 1, "erp_url": leave_pull_doc.source_erp},
			fields=["name"],
			limit=1
		)
		
		if not sync_settings:
			frappe.log_error(
				"No ERP Sync Settings found for source ERP: {0}".format(leave_pull_doc.source_erp),
				"Leave Status Sync Error"
			)
			return
		
		# Prepare status update data
		status_data = {
			"leave_id": leave_pull_doc.leave_id,
			"status": leave_pull_doc.status,
			"approved_from_date": str(leave_pull_doc.approved_from_date) if leave_pull_doc.approved_from_date else None,
			"approved_to_date": str(leave_pull_doc.approved_to_date) if leave_pull_doc.approved_to_date else None,
			"total_no_of_approved_days": leave_pull_doc.total_no_of_approved_days
		}
		
		# Get sync settings
		settings = frappe.get_doc("ERP Sync Settings", sync_settings[0].name)
		
		# Send status update directly (not queued for immediate update)
		success = send_to_remote_erp(
			settings.erp_url,
			settings.get_password("api_key"),
			settings.get_password("api_secret"),
			"Leave Status Update",
			status_data,
			"Status Update"
		)
		
		if success:
			frappe.log_error(
				message="Successfully synced Leave status for {0} to source ERP".format(leave_pull_doc.leave_id),
				title="Leave Status Sync Success"
			)
		else:
			frappe.log_error(
				message="Failed to sync Leave status for {0} to source ERP".format(leave_pull_doc.leave_id),
				title="Leave Status Sync Failed"
			)
		
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error syncing Leave status to source for {0}".format(leave_pull_doc.leave_id)
		)


def push_expense_status_to_source(expense_pull_doc):
	"""
	Push Expense Pull approval status back to source ERP's OTPL Expense
	Called from Expense Pull on_update hook
	Syncs primarily the approved_by_manager checkbox
	"""
	try:
		# Check if this record came from an external source
		if not expense_pull_doc.source_erp:
			return
		
		# Prevent infinite sync loop
		if hasattr(expense_pull_doc, 'flags') and expense_pull_doc.flags.get('ignore_sync'):
			return
		
		# Find the ERP Sync Settings for the source ERP
		sync_settings = frappe.get_all(
			"ERP Sync Settings",
			filters={"enabled": 1, "erp_url": expense_pull_doc.source_erp},
			fields=["name"],
			limit=1
		)
		
		if not sync_settings:
			frappe.log_error(
				"No ERP Sync Settings found for source ERP: {0}".format(expense_pull_doc.source_erp),
				"Expense Approval Sync Error"
			)
			return
		
		# Prepare approval update data - primarily checkbox based
		status_data = {
			"expense_id": expense_pull_doc.expense_id,
			"approved_by_manager": expense_pull_doc.approved_by_manager,  # Primary field
			"amount_approved": expense_pull_doc.amount_approved,
			"status": expense_pull_doc.status  # Optional field
		}
		
		# Get sync settings
		settings = frappe.get_doc("ERP Sync Settings", sync_settings[0].name)
		
		# Send approval update directly (not queued for immediate update)
		success = send_to_remote_erp(
			settings.erp_url,
			settings.get_password("api_key"),
			settings.get_password("api_secret"),
			"Expense Status Update",
			status_data,
			"Status Update"
		)
		
		if success:
			frappe.log_error(
				message="Successfully synced Expense approval for {0} to source ERP".format(expense_pull_doc.expense_id),
				title="Expense Approval Sync Success"
			)
		else:
			frappe.log_error(
				message="Failed to sync Expense approval for {0} to source ERP".format(expense_pull_doc.expense_id),
				title="Expense Approval Sync Failed"
			)
		
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error syncing Expense approval to source for {0}".format(expense_pull_doc.expense_id)
		)
