import frappe
import json
from frappe import _
# from frappe.utils import pretty_date, getdate, fmt_money
from employee_self_service.mobile.v1.api_utils import (
    gen_response,
    ess_validate,
    exception_handler,
    get_employee_by_user,
    get_global_defaults
)
from frappe.utils import get_datetime,fmt_money, today


@frappe.whitelist()
def create_expense(**data):
    try:
        from frappe.handler import upload_file

        emp_data = get_employee_by_user(
        frappe.session.user, fields=["name", "image", "department","company", "sales_order","external_so"]
        )
        if not len(emp_data) >= 1:
            return gen_response(500, "Employee does not exists")
        msg = ""
        if data.get("name"):
            expense_doc = frappe.get_doc("OTPL Expense",data.get("name"))
            msg = "Expense updated successfully"
        else:
            expense_doc = frappe.new_doc("OTPL Expense")
            expense_doc.sent_by = emp_data.get("name")
            expense_doc.date_of_entry = today()
            expense_doc.sales_order = emp_data.get("sales_order")
            expense_doc.external_sales_order = emp_data.get("external_so")
            msg = "Expense create successfully"
        expense_doc.update(data)
        expense_doc.save()
        if "file" in frappe.request.files:
            file = upload_file()
            file.attached_to_doctype = "OTPL Expense"
            file.attached_to_name = expense_doc.name
            file.save(ignore_permissions=True)
            frappe.db.set_value("OTPL Expense",expense_doc.name,"invoice_upload",file.file_url)
        return gen_response(200, msg)
    except frappe.PermissionError:
            return gen_response(500, "Not permitted to perform this action")
    except Exception as e:
            return exception_handler(e)

@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_expense_list(start=0, page_length=10, filters={}):
    try:
        global_defaults = get_global_defaults()
        emp_data = get_employee_by_user(
        frappe.session.user, fields=["name", "image", "department","company"]
        )
        if isinstance(filters,str):
            filters = json.loads(filters)
        filters.append(["sent_by", "=", emp_data.get("name")])
        expense_list = frappe.get_list(
            "OTPL Expense",
            fields=["*"],
            start=start,
            page_length=page_length,
            order_by="modified desc",
            filters=filters,
        )
        for row in expense_list:
            row["amount"] = fmt_money(
                    row.get("amount"),
                    currency=global_defaults.get("default_currency"),
                )
            row["amount_approved"] = fmt_money(
                    row.get("amount_approved"),
                    currency=global_defaults.get("default_currency"),
                )
        return gen_response(200, "Expense List getting Successfully", expense_list)
    except frappe.PermissionError:
        return gen_response(500, "Not permitted read OTPL Expense")
    except Exception as e:
        return exception_handler(e)
    
@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_expense_details(**data):
    try:
        global_defaults = get_global_defaults()
        expense_doc = json.loads(frappe.get_doc("OTPL Expense",data.get("name")).as_json())
        expense_doc["amount"] = fmt_money(
                expense_doc.get("amount"),
                currency=global_defaults.get("default_currency"),
            )
        expense_doc["amount_approved"] = fmt_money(
                expense_doc.get("amount_approved"),
                currency=global_defaults.get("default_currency"),
            )
        return gen_response(200, "Expense get successfully", expense_doc)
    except frappe.PermissionError:
        return gen_response(500, "Not permitted for read OTPL Expense")
    except Exception as e:
        return exception_handler(e)

@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_sales_order(start=0, page_length=10, filters=None):
    try:
        sales_order = frappe.get_all(
            "Sales Order",
            fields=["name"],
            start=start,
            page_length=page_length,
            order_by="modified desc",
            filters=filters,
        )
        return gen_response(200, "Sales order get successfully", sales_order)
    except Exception as e:
        return exception_handler(e)
    

@frappe.whitelist()
def get_expense_type():
    try:
        expense_types = frappe.get_all(
            "OTPL Expense Type", filters={}, fields=["name"]
        )
        return gen_response(200, "Expense type get successfully", expense_types)
    except Exception as e:
        return exception_handler(e)