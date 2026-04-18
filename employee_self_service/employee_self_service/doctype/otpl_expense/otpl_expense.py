# -*- coding: utf-8 -*-
# Copyright (c) 2024, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from frappe.utils import flt, today
from employee_self_service.employee_self_service.utils.erp_sync import push_expense_to_remote_erp


class OTPLExpense(Document):
	def validate(self):
		if not self.approval_manager or not self.external_manager:
			employee_doc = frappe.get_doc("Employee", self.sent_by)
			if employee_doc.external_reporting_manager == 1:
				external_report_to = employee_doc.external_report_to
				report_to = frappe.db.get_value("Employee Pull", external_report_to, "employee")
				self.is_external_manager = 1
				self.external_manager = report_to
				self.approval_manager = ""
			else:
				if employee_doc.reports_to:
					user = frappe.db.get_value("Employee", employee_doc.reports_to, "user_id")
					if user:
						self.is_external_manager = 0
						self.external_manager = ""
						self.approval_manager = user
		if not self.business_line:
			self.business_line = frappe.db.get_value("Employee", self.sent_by, "business_vertical")

		# Calculate item totals for GST expense categories
		if self.expense_category in ("With GST Invoice", "Without GST Invoice"):
			self.calculate_item_totals()

		# Validate amount_approved cannot exceed original amount (only for non-GST categories)
		if self.expense_category not in ("With GST Invoice", "Without GST Invoice"):
			if self.amount_approved and self.amount:
				if self.amount_approved > self.amount:
					frappe.throw("Amount Approved cannot be greater than the original Amount")

		# Validate transfer_to_employee is set for Other Employee Transfer
		if self.expense_category == "Other Employee Transfer" and not self.transfer_to_employee:
			frappe.throw("Please select the employee to whom the fund is being transferred.")

	def calculate_item_totals(self):
		"""Calculate totals from expense_items table and compute tax from taxes_and_charges template"""
		total_amount = 0
		for row in self.get("expense_items", []):
			base = flt(row.rate) * flt(row.quantity)
			row.amount = base
			total_amount += base

		self.total_amount = total_amount

		# Calculate tax from the selected Purchase Taxes and Charges Template
		total_tax = 0
		if self.taxes_and_charges:
			tax_rows = frappe.get_all(
				"Purchase Taxes and Charges",
				filters={"parent": self.taxes_and_charges, "parenttype": "Purchase Taxes and Charges Template"},
				fields=["charge_type", "rate"],
				order_by="idx asc"
			)
			for tax in tax_rows:
				if tax.charge_type == "On Net Total":
					total_tax += flt(total_amount) * flt(tax.rate) / 100
				elif tax.charge_type == "On Previous Row Total":
					total_tax += flt(total_amount + total_tax) * flt(tax.rate) / 100

		self.total_gst_amount = total_tax
		self.total_with_gst = total_amount + total_tax
		# Auto-set approved amount from grand total
		self.amount_approved = self.total_with_gst

	def on_update(self):
		"""Trigger sync to remote ERP when expense is saved with external manager"""
		push_expense_to_remote_erp(self)

	def approve_expense(self):
		if not frappe.session.user == self.approval_manager and not frappe.session.user == "Administrator":
			frappe.throw("Only the assigned manager can approve this expense.")

		# --- Validate all required fields before approval ---
		missing = []

		if not self.sent_by:
			missing.append("Sent By")
		if not self.date_of_entry:
			missing.append("Date of Entry")
		if not self.date_of_expense:
			missing.append("Date of Expense")
		if not self.expense_claim_type:
			missing.append("Expense Claim Type")
		if not self.expense_category:
			missing.append("Expense Category")
		if not self.business_line:
			missing.append("Business Line")
		if not self.amount or flt(self.amount) <= 0:
			missing.append("Amount")

		if self.expense_category in ("With GST Invoice", "Without GST Invoice"):
			if not self.gst_number:
				missing.append("GST Number")
			if not self.supplier:
				missing.append("Supplier")
			if not self.get("expense_items") or len(self.get("expense_items")) == 0:
				missing.append("Expense Items (at least one item row)")
			else:
				for idx, row in enumerate(self.get("expense_items"), 1):
					if not row.item:
						missing.append(f"Item in Row {idx}")
					if not row.quantity or flt(row.quantity) <= 0:
						missing.append(f"Quantity in Row {idx}")
					if not row.rate or flt(row.rate) <= 0:
						missing.append(f"Rate in Row {idx}")
			if not self.total_amount or flt(self.total_amount) <= 0:
				missing.append("Total Amount must be greater than zero")

		elif self.expense_category == "Other Employee Transfer":
			if not self.transfer_to_employee:
				missing.append("Transfer To Employee")
			if not self.amount_approved or flt(self.amount_approved) <= 0:
				missing.append("Amount Approved")

		else:
			# With Cash Memo or no category (backward compat)
			if not self.amount_approved or flt(self.amount_approved) <= 0:
				missing.append("Amount Approved")

		if missing:
			frappe.throw(
				"The following fields are required before approval:<br><br>• " + "<br>• ".join(missing),
				title="Missing Required Fields"
			)

		self.approved_by_manager = 1
		self.save()

	def on_submit(self):
		if not self.approved_by_manager:
			frappe.throw("Expense cannot be submitted without manager approval.")

		if self.expense_category in ("With GST Invoice", "Without GST Invoice"):
			self.create_material_request_and_po()
		elif self.expense_category == "With Cash Memo":
			self.create_expense_jv()
		elif self.expense_category == "Other Employee Transfer":
			self.create_transfer_jv()
		else:
			# Default: existing JV flow for backward compatibility
			self.create_expense_jv()

	def on_cancel(self):
		# Cancel any Journal Entries / Payment Entries linked to this OTPL Expense
		filters = {"otpl_ref_doctype": self.doctype, "otpl_ref_name": self.name, "docstatus": 1}
		jv_list = frappe.get_all("Journal Entry", filters=filters, fields=["name"])
		for row in jv_list:
			jv_doc = frappe.get_doc("Journal Entry", row.name)
			jv_doc.flags.ignore_permissions = True
			jv_doc.cancel()

		pe_list = frappe.get_all("Payment Entry", filters=filters, fields=["name"])
		for row in pe_list:
			pe_doc = frappe.get_doc("Payment Entry", row.name)
			pe_doc.flags.ignore_permissions = True
			pe_doc.cancel()

		# Cancel Purchase Invoice if linked
		if self.get("purchase_invoice"):
			pi_doc = frappe.get_doc("Purchase Invoice", self.purchase_invoice)
			if pi_doc.docstatus == 1:
				pi_doc.flags.ignore_permissions = True
				pi_doc.cancel()

		# Cancel Purchase Receipt if linked
		if self.get("purchase_receipt"):
			pr_doc = frappe.get_doc("Purchase Receipt", self.purchase_receipt)
			if pr_doc.docstatus == 1:
				pr_doc.flags.ignore_permissions = True
				pr_doc.cancel()

		# Cancel Purchase Order if linked
		if self.purchase_order:
			po_doc = frappe.get_doc("Purchase Order", self.purchase_order)
			if po_doc.docstatus == 1:
				po_doc.flags.ignore_permissions = True
				po_doc.cancel()

		# Cancel Material Request if linked
		if self.material_request:
			mr_doc = frappe.get_doc("Material Request", self.material_request)
			if mr_doc.docstatus == 1:
				mr_doc.flags.ignore_permissions = True
				mr_doc.cancel()

	def after_delete(self):
		# remove linked Journal Entries / Payment Entries if force deleting
		filters = {"otpl_ref_doctype": self.doctype, "otpl_ref_name": self.name}
		jv_list = frappe.get_all("Journal Entry", filters=filters, fields=["name"])
		for row in jv_list:
			frappe.delete_doc("Journal Entry", row.name, ignore_permissions=True, force=1)

		pe_list = frappe.get_all("Payment Entry", filters=filters, fields=["name"])
		for row in pe_list:
			frappe.delete_doc("Payment Entry", row.name, ignore_permissions=True, force=1)

		# Delete linked Purchase Invoice
		if self.get("purchase_invoice"):
			frappe.delete_doc("Purchase Invoice", self.purchase_invoice, ignore_permissions=True, force=1)
		# Delete linked Purchase Receipt
		if self.get("purchase_receipt"):
			frappe.delete_doc("Purchase Receipt", self.purchase_receipt, ignore_permissions=True, force=1)
		# Delete linked Purchase Order
		if self.purchase_order:
			frappe.delete_doc("Purchase Order", self.purchase_order, ignore_permissions=True, force=1)
		# Delete linked Material Request
		if self.material_request:
			frappe.delete_doc("Material Request", self.material_request, ignore_permissions=True, force=1)

	# ─── GST Invoice flow: Material Request + Purchase Order ───

	def create_material_request_and_po(self):
		"""Create Material Request (auto-submitted) and Purchase Order (auto-submitted) for GST expense categories.
		Uses frappe.get_meta() to dynamically detect fields on PO since custom fields are not in fixtures."""
		if not self.expense_items or len(self.expense_items) == 0:
			frappe.throw("Please add items in the Expense Items table before submitting.")
		if not self.supplier:
			frappe.throw("Please select a Supplier before submitting.")

		company = frappe.db.get_value("Global Defaults", "Global Defaults", "default_company")

		# Get target warehouse from the employee making this entry (Warehouse has employee link field)
		employee_warehouse = frappe.db.get_value("Warehouse", {"employee": self.sent_by, "is_group": 0}, "name")
		if not employee_warehouse:
			# Fallback: stock settings default
			employee_warehouse = frappe.db.get_single_value("Stock Settings", "default_warehouse")
		if not employee_warehouse:
			employee_warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")
		if not employee_warehouse:
			frappe.throw("No warehouse found for this employee. Please assign a warehouse.")

		expense_date = self.date_of_expense or self.date_of_entry or today()

		# --- Create Material Request (Material Indent) ---
		mr_items = []
		for row in self.expense_items:
			stock_uom = frappe.db.get_value("Item", row.item, "stock_uom") or "Nos"
			mr_items.append({
				"item_code": row.item,
				"item_name": row.item_name,
				"qty": row.quantity,
				"schedule_date": expense_date,
				"warehouse": employee_warehouse,
				"uom": stock_uom,
				"stock_uom": stock_uom,
			})

		mr_doc = frappe.get_doc({
			"doctype": "Material Request",
			"material_request_type": "Purchase",
			"company": company,
			"transaction_date": expense_date,
			"schedule_date": expense_date,
			"items": mr_items,
		})
		mr_doc.flags.ignore_permissions = True
		mr_doc.flags.ignore_mandatory = True
		mr_doc.insert()
		mr_doc.submit()

		frappe.db.set_value(self.doctype, self.name, "material_request", mr_doc.name)

		# --- Create Purchase Order (auto-submitted) ---
		po_items = []
		for idx, row in enumerate(self.expense_items):
			stock_uom = frappe.db.get_value("Item", row.item, "stock_uom") or "Nos"
			po_items.append({
				"item_code": row.item,
				"item_name": row.item_name,
				"qty": row.quantity,
				"rate": row.rate,
				"schedule_date": expense_date,
				"warehouse": employee_warehouse,
				"uom": stock_uom,
				"stock_uom": stock_uom,
				"material_request": mr_doc.name,
				"material_request_item": mr_doc.items[idx].name if idx < len(mr_doc.items) else None,
			})

		po_data = {
			"doctype": "Purchase Order",
			"supplier": self.supplier,
			"company": company,
			"transaction_date": expense_date,
			"schedule_date": expense_date,
			"set_warehouse": employee_warehouse,
			"items": po_items,
		}

		# Use metadata to dynamically set custom fields on PO
		po_meta = frappe.get_meta("Purchase Order")
		po_field_names = {f.fieldname for f in po_meta.fields}

		if "prepaid_percentage" in po_field_names:
			po_data["prepaid_percentage"] = 100

		if "supplier_mobile_whatsapp_number" in po_field_names:
			po_data["supplier_mobile_whatsapp_number"] = "9999999999"

		if "contact_mobile" in po_field_names:
			po_data["contact_mobile"] = "9999999999"

		if "business_line" in po_field_names and self.business_line:
			po_data["business_line"] = self.business_line

		if "material_already_received" in po_field_names:
			po_data["material_already_received"] = "Yes"

		# Set tax template and let taxes be calculated
		if self.taxes_and_charges:
			po_data["taxes_and_charges"] = self.taxes_and_charges
			# Fetch tax rows from the template and populate the taxes child table
			tax_rows = frappe.get_all(
				"Purchase Taxes and Charges",
				filters={"parent": self.taxes_and_charges, "parenttype": "Purchase Taxes and Charges Template"},
				fields=["charge_type", "account_head", "rate", "description", "cost_center", "tax_amount",
						"add_deduct_tax", "category", "included_in_print_rate"],
				order_by="idx asc"
			)
			if tax_rows:
				po_data["taxes"] = []
				for tax in tax_rows:
					po_data["taxes"].append({
						"charge_type": tax.charge_type,
						"account_head": tax.account_head,
						"rate": tax.rate,
						"description": tax.description or tax.account_head,
						"cost_center": tax.cost_center,
						"add_deduct_tax": tax.add_deduct_tax or "Add",
						"category": tax.category or "Total",
						"included_in_print_rate": tax.included_in_print_rate,
					})

		po_doc = frappe.get_doc(po_data)
		po_doc.flags.ignore_permissions = True
		po_doc.flags.ignore_mandatory = True
		# Flag to skip the hook — we'll create PR/PI/PE directly below
		po_doc.flags.skip_otpl_auto_entries = True
		po_doc.insert()
		po_doc.submit()

		frappe.db.set_value(self.doctype, self.name, "purchase_order", po_doc.name)

		# --- Create Purchase Receipt (auto-submitted) ---
		pr_doc = self._create_purchase_receipt(po_doc, company, employee_warehouse)
		frappe.db.set_value(self.doctype, self.name, "purchase_receipt", pr_doc.name)

		# --- Create Purchase Invoice (auto-submitted) ---
		pi_doc = self._create_purchase_invoice(po_doc, pr_doc, company, employee_warehouse)
		frappe.db.set_value(self.doctype, self.name, "purchase_invoice", pi_doc.name)

		# --- Create Payment Entry (auto-submitted) ---
		pe_doc = self._create_payment_entry(pi_doc, company)
		frappe.db.set_value(self.doctype, self.name, "payment_entry", pe_doc.name)

		frappe.msgprint(
			f"Material Request <b>{mr_doc.name}</b>, Purchase Order <b>{po_doc.name}</b>, "
			f"Purchase Receipt <b>{pr_doc.name}</b>, Purchase Invoice <b>{pi_doc.name}</b>, "
			f"and Payment Entry <b>{pe_doc.name}</b> created and submitted automatically.",
			title="All Entries Created",
			indicator="green"
		)

	def _create_purchase_receipt(self, po_doc, company, target_warehouse):
		"""Create Purchase Receipt from submitted PO using metadata for custom fields."""
		pr_meta = frappe.get_meta("Purchase Receipt")
		pr_field_names = {f.fieldname for f in pr_meta.fields}

		pr_items = []
		for item in po_doc.items:
			pr_items.append({
				"item_code": item.item_code,
				"item_name": item.item_name,
				"qty": item.qty,
				"rate": item.rate,
				"warehouse": target_warehouse or item.warehouse,
				"uom": item.uom,
				"stock_uom": item.stock_uom,
				"purchase_order": po_doc.name,
				"purchase_order_item": item.name,
			})

		pr_data = {
			"doctype": "Purchase Receipt",
			"supplier": po_doc.supplier,
			"company": company,
			"posting_date": today(),
			"set_warehouse": target_warehouse,
			"items": pr_items,
		}

		if "business_line" in pr_field_names and self.business_line:
			pr_data["business_line"] = self.business_line

		if po_doc.taxes_and_charges:
			pr_data["taxes_and_charges"] = po_doc.taxes_and_charges
			if po_doc.taxes:
				pr_data["taxes"] = []
				for tax in po_doc.taxes:
					pr_data["taxes"].append({
						"charge_type": tax.charge_type,
						"account_head": tax.account_head,
						"rate": tax.rate,
						"description": tax.description or tax.account_head,
						"cost_center": tax.cost_center,
						"add_deduct_tax": tax.add_deduct_tax or "Add",
						"category": tax.category or "Total",
						"included_in_print_rate": tax.included_in_print_rate,
					})

		pr_doc = frappe.get_doc(pr_data)
		pr_doc.flags.ignore_permissions = True
		pr_doc.flags.ignore_mandatory = True
		pr_doc.insert()
		pr_doc.submit()
		return pr_doc

	def _create_purchase_invoice(self, po_doc, pr_doc, company, target_warehouse):
		"""Create Purchase Invoice from submitted PO + PR using metadata for custom fields."""
		pi_meta = frappe.get_meta("Purchase Invoice")
		pi_field_names = {f.fieldname for f in pi_meta.fields}

		pi_items = []
		for idx, item in enumerate(po_doc.items):
			pi_item = {
				"item_code": item.item_code,
				"item_name": item.item_name,
				"qty": item.qty,
				"rate": item.rate,
				"warehouse": target_warehouse or item.warehouse,
				"uom": item.uom,
				"stock_uom": item.stock_uom,
				"purchase_order": po_doc.name,
				"po_detail": item.name,
				"purchase_receipt": pr_doc.name,
			}
			if idx < len(pr_doc.items):
				pi_item["pr_detail"] = pr_doc.items[idx].name
			pi_items.append(pi_item)

		pi_data = {
			"doctype": "Purchase Invoice",
			"supplier": po_doc.supplier,
			"company": company,
			"posting_date": today(),
			"set_warehouse": target_warehouse,
			"update_stock": 0,
			"items": pi_items,
		}

		if "business_line" in pi_field_names and self.business_line:
			pi_data["business_line"] = self.business_line

		if po_doc.taxes_and_charges:
			pi_data["taxes_and_charges"] = po_doc.taxes_and_charges
			if po_doc.taxes:
				pi_data["taxes"] = []
				for tax in po_doc.taxes:
					pi_data["taxes"].append({
						"charge_type": tax.charge_type,
						"account_head": tax.account_head,
						"rate": tax.rate,
						"description": tax.description or tax.account_head,
						"cost_center": tax.cost_center,
						"add_deduct_tax": tax.add_deduct_tax or "Add",
						"category": tax.category or "Total",
						"included_in_print_rate": tax.included_in_print_rate,
					})

		pi_doc = frappe.get_doc(pi_data)
		pi_doc.flags.ignore_permissions = True
		pi_doc.flags.ignore_mandatory = True
		pi_doc.insert()
		pi_doc.submit()
		return pi_doc

	def _create_payment_entry(self, pi_doc, company):
		"""Create Payment Entry against the Purchase Invoice using metadata for custom fields."""
		creditor_account = None
		if self.business_line:
			creditor_account = frappe.db.get_value("Business Line", self.business_line, "supplier_creditor_account")
		if not creditor_account:
			creditor_account = frappe.db.get_value("Company", company, "default_payable_account")
		if not creditor_account:
			frappe.throw(f"No supplier creditor account configured for Business Line {self.business_line} or company default.")

		bank_account = frappe.db.get_value("Company", company, "default_bank_account")
		if not bank_account:
			frappe.throw("No default bank account configured for the company.")

		paid_amount = flt(pi_doc.grand_total)

		pe_data = {
			"doctype": "Payment Entry",
			"payment_type": "Pay",
			"party_type": "Supplier",
			"party": self.supplier,
			"company": company,
			"posting_date": today(),
			"paid_from": bank_account,
			"paid_to": creditor_account,
			"paid_amount": paid_amount,
			"received_amount": paid_amount,
			"reference_no": self.name,
			"reference_date": today(),
			"references": [{
				"reference_doctype": "Purchase Invoice",
				"reference_name": pi_doc.name,
				"total_amount": paid_amount,
				"outstanding_amount": paid_amount,
				"allocated_amount": paid_amount,
			}],
		}

		pe_meta = frappe.get_meta("Payment Entry")
		pe_field_names = {f.fieldname for f in pe_meta.fields}

		if "otpl_ref_doctype" in pe_field_names:
			pe_data["otpl_ref_doctype"] = self.doctype
		if "otpl_ref_name" in pe_field_names:
			pe_data["otpl_ref_name"] = self.name

		pe_doc = frappe.get_doc(pe_data)
		pe_doc.flags.ignore_permissions = True
		pe_doc.flags.ignore_mandatory = True
		pe_doc.insert()
		pe_doc.submit()
		return pe_doc

	# ─── Cash Memo flow: Expense Journal Entry ───

	def create_expense_jv(self):
		"""Create Journal Entry for expense (Cash Memo or default flow)"""
		if not self.amount_approved or self.amount_approved <= 0:
			frappe.throw("Amount Approved must be greater than zero to create Journal Entry.")

		expense_account = None
		if self.expense_claim_type and self.business_line:
			expense_account = frappe.db.get_value(
				"OTPL Expense Type Accounts",
				{"parent": self.expense_claim_type, "business_line": self.business_line},
				"expense_account"
			)

		if not expense_account:
			frappe.throw("Expense account not configured for selected Expense Type and Business Line.")

		payable_account = frappe.db.get_value("Business Line", self.business_line, "payroll_payable")
		if not payable_account:
			frappe.throw("Payroll Payable (employee payable) account not configured on Business Line.")

		company = frappe.db.get_value("Global Defaults", "Global Defaults", "default_company")
		order_cost_center = frappe.db.get_value("Cost Center", {"sales_order": self.sales_order}, "name")

		jv_doc = frappe.get_doc({
			"doctype": "Journal Entry",
			"posting_date": self.date_of_expense or self.date_of_entry,
			"voucher_type": "Journal Entry",
			"company": company,
			"user_remark": self.details_of_expense or self.purpose,
			"accounts": [
				{
					"account": expense_account,
					"debit_in_account_currency": self.amount_approved,
					"cost_center": order_cost_center
				},
				{
					"account": payable_account,
					"credit_in_account_currency": self.amount_approved,
					"party_type": "Employee",
					"party": self.sent_by,
					"cost_center": order_cost_center
				}
			],
		})

		jv_doc.otpl_ref_doctype = self.doctype
		jv_doc.otpl_ref_name = self.name
		jv_doc.flags.ignore_mandatory = True
		jv_doc.flags.ignore_permissions = True
		jv_doc = jv_doc.insert()
		jv_doc.submit()

		frappe.db.set_value(self.doctype, self.name, "journal_entry", jv_doc.name)

	# ─── Other Employee Transfer flow: Transfer JV ───

	def create_transfer_jv(self):
		"""Create Journal Entry to transfer funds to another employee"""
		if not self.transfer_to_employee:
			frappe.throw("Please select the employee to transfer funds to.")
		if not self.amount_approved or self.amount_approved <= 0:
			frappe.throw("Amount Approved must be greater than zero to create transfer Journal Entry.")

		# Fetch payroll payable for the sender (giving funds) via their business vertical
		sender_business_vertical = frappe.db.get_value("Employee", self.sent_by, "business_vertical")
		if not sender_business_vertical:
			frappe.throw(f"Business Vertical not set for employee {self.sent_by}.")
		sender_payable = frappe.db.get_value("Business Line", sender_business_vertical, "payroll_payable")
		if not sender_payable:
			frappe.throw(f"Payroll Payable account not configured on Business Line {sender_business_vertical}.")

		# Fetch payroll payable for the receiver (receiving funds) via their business vertical
		receiver_business_vertical = frappe.db.get_value("Employee", self.transfer_to_employee, "business_vertical")
		if not receiver_business_vertical:
			frappe.throw(f"Business Vertical not set for employee {self.transfer_to_employee}.")
		receiver_payable = frappe.db.get_value("Business Line", receiver_business_vertical, "payroll_payable")
		if not receiver_payable:
			frappe.throw(f"Payroll Payable account not configured on Business Line {receiver_business_vertical}.")

		company = frappe.db.get_value("Global Defaults", "Global Defaults", "default_company")
		order_cost_center = frappe.db.get_value("Cost Center", {"sales_order": self.sales_order}, "name")

		transfer_employee_name = frappe.db.get_value("Employee", self.transfer_to_employee, "employee_name")

		jv_doc = frappe.get_doc({
			"doctype": "Journal Entry",
			"posting_date": self.date_of_expense or self.date_of_entry,
			"voucher_type": "Journal Entry",
			"company": company,
			"user_remark": f"Transfer from {self.employee_name or self.sent_by} to {transfer_employee_name or self.transfer_to_employee} - {self.details_of_expense or self.purpose}",
			"accounts": [
				{
					"account": receiver_payable,
					"debit_in_account_currency": self.amount_approved,
					"party_type": "Employee",
					"party": self.transfer_to_employee,
					"cost_center": order_cost_center
				},
				{
					"account": sender_payable,
					"credit_in_account_currency": self.amount_approved,
					"party_type": "Employee",
					"party": self.sent_by,
					"cost_center": order_cost_center
				}
			],
		})

		jv_doc.otpl_ref_doctype = self.doctype
		jv_doc.otpl_ref_name = self.name
		jv_doc.flags.ignore_mandatory = True
		jv_doc.flags.ignore_permissions = True
		jv_doc = jv_doc.insert()
		jv_doc.submit()

		frappe.db.set_value(self.doctype, self.name, "journal_entry", jv_doc.name)
		frappe.msgprint(
			f"Transfer Journal Entry <b>{jv_doc.name}</b> created and submitted.",
			title="Transfer Entry Created",
			indicator="green"
		)


@frappe.whitelist()
def get_tax_details(template_name):
	"""Fetch tax rows from a Purchase Taxes and Charges Template"""
	if not template_name:
		return []
	return frappe.get_all(
		"Purchase Taxes and Charges",
		filters={"parent": template_name, "parenttype": "Purchase Taxes and Charges Template"},
		fields=["charge_type", "rate"],
		order_by="idx asc"
	)


@frappe.whitelist()
def get_supplier_by_gstin(gstin):
	"""Look up a Supplier by GST number from linked Address records"""
	if not gstin:
		return None

	result = frappe.db.sql("""
		SELECT dl.link_name as supplier
		FROM `tabAddress` a
		INNER JOIN `tabDynamic Link` dl ON dl.parent = a.name AND dl.parenttype = 'Address'
		WHERE a.gstin = %s AND dl.link_doctype = 'Supplier'
		LIMIT 1
	""", gstin, as_dict=1)

	if result:
		return result[0].supplier
	return None


def on_purchase_order_submit(doc, method):
	"""Hook: when a Purchase Order linked to an OTPL Expense is submitted externally
	(not from the OTPL Expense on_submit flow), auto-create Purchase Receipt, Purchase Invoice, and Payment Entry.
	Uses frappe.get_meta() to dynamically detect fields since custom fields are not in fixtures."""

	# Skip if already handled inside create_material_request_and_po
	if getattr(doc.flags, "skip_otpl_auto_entries", False):
		return

	expense_name = frappe.db.get_value(
		"OTPL Expense",
		{"purchase_order": doc.name, "docstatus": 1},
		"name"
	)
	if not expense_name:
		return

	expense_doc = frappe.get_doc("OTPL Expense", expense_name)
	company = doc.company
	target_warehouse = doc.set_warehouse or (doc.items[0].warehouse if doc.items else None)

	# Create PR, PI, PE via the expense doc methods
	pr_doc = expense_doc._create_purchase_receipt(doc, company, target_warehouse)
	frappe.db.set_value("OTPL Expense", expense_name, "purchase_receipt", pr_doc.name)

	pi_doc = expense_doc._create_purchase_invoice(doc, pr_doc, company, target_warehouse)
	frappe.db.set_value("OTPL Expense", expense_name, "purchase_invoice", pi_doc.name)

	pe_doc = expense_doc._create_payment_entry(pi_doc, company)
	frappe.db.set_value("OTPL Expense", expense_name, "payment_entry", pe_doc.name)

	frappe.msgprint(
		f"Purchase Receipt <b>{pr_doc.name}</b>, Purchase Invoice <b>{pi_doc.name}</b>, "
		f"and Payment Entry <b>{pe_doc.name}</b> auto-created from OTPL Expense <b>{expense_name}</b>.",
		title="Auto Entries Created",
		indicator="green"
	)


def _create_payment_entry_for_expense(expense_doc, pi_doc, company):
	"""Deprecated — kept for backward compatibility. Use OTPLExpense._create_payment_entry instead."""
	return expense_doc._create_payment_entry(pi_doc, company)
	