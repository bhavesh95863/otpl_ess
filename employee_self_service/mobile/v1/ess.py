import json
import os
import calendar
import frappe
import datetime

from frappe import _
from frappe.auth import LoginManager
from frappe.utils import (
    cstr,
    today,
    nowdate,
    getdate,
    now_datetime,
    get_first_day,
    get_last_day,
    date_diff,
    flt,
    pretty_date,
    fmt_money,
    add_days,
    format_time,
)
from employee_self_service.mobile.v1.api_utils import (
    gen_response,
    generate_key,
    ess_validate,
    get_employee_by_user,
    validate_employee_data,
    get_ess_settings,
    get_global_defaults,
    exception_handler,
)
from frappe.handler import upload_file
from erpnext.accounts.utils import get_fiscal_year

from employee_self_service.employee_self_service.doctype.push_notification.push_notification import (
    create_push_notification,
)
from erpnext.hr.doctype.leave_application.leave_application import (
    get_leave_balance_on,
    get_leaves_for_period,
)
from frappe.utils import add_to_date, get_datetime

DATE_FORMAT = "%Y-%m-%d"


def get_date_str(date_obj):
    """Return the given datetime like object (datetime.date, datetime.datetime, string) as a string in `yyyy-mm-dd` format."""
    if isinstance(date_obj, str):
        date_obj = get_datetime(date_obj)
    return date_obj.strftime(DATE_FORMAT)


@frappe.whitelist(allow_guest=True)
def login(usr, pwd, unique_id=None):
    try:
        login_manager = LoginManager()
        login_manager.authenticate(usr, pwd)
        validate_employee(login_manager.user)
        emp_data = get_employee_by_user(login_manager.user)
        # Register device (throws exception if device is not valid)
        if unique_id:
            if not register_device(emp_data.get("name"), unique_id):
                return

        login_manager.post_login()
        if frappe.response["message"] == "Logged In":
            frappe.response["user"] = login_manager.user
            frappe.response["key_details"] = generate_key(login_manager.user)
            frappe.response["employee_id"] = emp_data.get("name")
        gen_response(200, frappe.response["message"])
    except frappe.AuthenticationError:
        gen_response(500, frappe.response["message"])
    except Exception as e:
        return exception_handler(e)


def register_device(employee, unique_id):
    # check if device registration exists for this employee
    # if not enter the given number and create registration
    # if exists than validate the given number with existing number
    # if number mataches than allow login
    # else through frappe exceptions
    try:
        ess_settings = get_ess_settings()
        if not ess_settings.get("enable_device_restrictions"):
            return True

        existing_registration = frappe.db.exists(
            "Employee Device Registration", {"employee": employee}
        )
        existing_unique_id = frappe.db.exists(
            "Employee Device Registration", {"unique_id": unique_id}
        )

        if existing_unique_id and not existing_registration:
            gen_response(500, "Device not recognized. Please contact admin.")
            return False

        if not existing_registration:
            # Register the device if not exists
            doc = frappe.new_doc("Employee Device Registration")
            doc.employee = employee
            doc.unique_id = unique_id
            doc.insert(ignore_permissions=True)
        else:
            # Fetch the existing device_id to compare
            registered_device_id = frappe.db.get_value(
                "Employee Device Registration", existing_registration, "unique_id"
            )
            if registered_device_id != unique_id:
                gen_response(500, "Device not recognized. Please contact admin.")
                return False
        return True
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "register_device_error")
        frappe.throw("An error occurred during device registration.")


def validate_employee(user):
    if not frappe.db.exists("Employee", dict(user_id=user)):
        frappe.response["message"] = "Please link Employee with this user"
        raise frappe.AuthenticationError(frappe.response["message"])


@frappe.whitelist()
@ess_validate(methods=["POST"])
def make_leave_application(*args, **kwargs):
    try:

        emp_data = get_employee_by_user(frappe.session.user)
        if not len(emp_data) >= 1:
            return gen_response(500, "Employee does not exists!")
        validate_employee_data(emp_data)
        # if not emp_data.get("leave_approver"):
        #     frappe.throw("Leave approver not selected in employee record.")
        leave_application_doc = frappe.get_doc(
            dict(
                doctype="OTPL Leave",
                employee=emp_data.get("name"),
                approved_from_date=emp_data.get("from_date"),
                approved_to_date=emp_data.get("to_date"),
                approver=emp_data.get("leave_approver"),
            )
        )
        leave_application_doc.update(kwargs)
        res = leave_application_doc.insert()
        gen_response(200, "Leave application successfully added!")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def update_leave_application(*args, **kwargs):
    try:
        emp_data = get_employee_by_user(frappe.session.user)
        if not len(emp_data) >= 1:
            return gen_response(500, "Employee does not exists!")
        validate_employee_data(emp_data)

        leave_id = kwargs.get("name")
        if not leave_id:
            return gen_response(500, "Leave ID is required!")

        if not frappe.db.exists("Leave Application", kwargs.get("name")):
            return gen_response(500, "Leave application does not exists!")

        leave_application_doc = frappe.get_doc("Leave Application", leave_id)
        leave_application_doc.update(kwargs)
        leave_application_doc.save()
        gen_response(200, "Leave application successfully updated!")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def cancel_leave_application(*args, **kwargs):
    try:
        emp_data = get_employee_by_user(frappe.session.user)
        if not len(emp_data) >= 1:
            return gen_response(500, "Employee does not exists!")
        validate_employee_data(emp_data)

        leave_id = kwargs.get("name")
        if not leave_id:
            return gen_response(500, "Leave ID is required!")

        if not frappe.db.exists("Leave Application", kwargs.get("name")):
            return gen_response(500, "Leave application does not exists!")

        leave_application_doc = frappe.get_doc("Leave Application", leave_id)
        if leave_application_doc.employee != emp_data.get("name"):
            return gen_response(
                500, "You are not authorized to cancel this leave application!"
            )
        leave_application_doc.status = "Cancelled"
        leave_application_doc.save()
        gen_response(200, "Leave application successfully cancelled!")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_leave_type(from_date=None, to_date=None):
    try:
        from erpnext.hr.doctype.leave_application.leave_application import (
            get_leave_balance_on,
        )

        if not from_date:
            from_date = today()
        emp_data = get_employee_by_user(frappe.session.user)
        leave_types = frappe.get_all(
            "Leave Type", filters={}, fields=["name", "'0' as balance"]
        )
        for leave_type in leave_types:
            leave_type["balance"] = get_leave_balance_on(
                emp_data.get("name"),
                leave_type.get("name"),
                from_date,
                consider_all_leaves_in_the_allocation_period=True,
            )
        return gen_response(200, "Leave type get successfully", leave_types)
    except Exception as e:
        return exception_handler(e)


# @frappe.whitelist()
# @ess_validate(methods=["GET"])
# def get_leave_application_list():
#     """
#     Get Leave Application which is already applied. Get Leave Balance Report
#     """
#     try:
#         emp_data = get_employee_by_user(frappe.session.user)
#         validate_employee_data(emp_data)
#         leave_application_fields = [
#             "name",
#             "leave_type",
#             "DATE_FORMAT(from_date, '%d-%m-%Y') as from_date",
#             "DATE_FORMAT(to_date, '%d-%m-%Y') as to_date",
#             "total_leave_days",
#             "description",
#             "status",
#             "DATE_FORMAT(posting_date, '%d-%m-%Y') as posting_date",
#         ]
#         upcoming_leaves = frappe.get_all(
#             "Leave Application",
#             filters={"from_date": [">", today()], "employee": emp_data.get("name")},
#             fields=leave_application_fields,
#         )

#         taken_leaves = frappe.get_all(
#             "Leave Application",
#             fields=leave_application_fields,
#             filters={"from_date": ["<=", today()], "employee": emp_data.get("name")},
#         )
#         fiscal_year = get_fiscal_year(nowdate())[0]
#         if not fiscal_year:
#             return gen_response(500, "Fiscal year not set")
#         res = get_leave_balance_report(
#             emp_data.get("name"), emp_data.get("company"), fiscal_year
#         )

#         leave_applications = {
#             "upcoming": upcoming_leaves,
#             "taken": taken_leaves,
#             "balance": res,
#         }
#         return gen_response(200, "Leave data getting successfully", leave_applications)
#     except Exception as e:
#         return exception_handler(e)

@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_leave_application_list():
    """
    Get Leave Application which is already applied. Get Leave Balance Report
    """
    try:
        emp_data = get_employee_by_user(frappe.session.user)
        validate_employee_data(emp_data)
        leave_application_fields = [
            "name",
            "'NA' as leave_type",
            "DATE_FORMAT(from_date, '%d-%m-%Y') as from_date",
            "DATE_FORMAT(to_date, '%d-%m-%Y') as to_date",
            "total_no_of_days as 'total_leave_days'",
            "reason as 'description'",
            "status",
            "DATE_FORMAT(creation, '%d-%m-%Y') as posting_date",
        ]
        upcoming_leaves = frappe.get_all(
            "OTPL Leave",
            filters={"from_date": [">", today()], "employee": emp_data.get("name")},
            fields=leave_application_fields,
        )

        taken_leaves = frappe.get_all(
            "OTPL Leave",
            fields=leave_application_fields,
            filters={"from_date": ["<=", today()], "employee": emp_data.get("name")},
        )
        fiscal_year = get_fiscal_year(nowdate())[0]
        if not fiscal_year:
            return gen_response(500, "Fiscal year not set")
        res = get_leave_balance_report(
            emp_data.get("name"), emp_data.get("company"), fiscal_year
        )

        leave_applications = {
            "upcoming": upcoming_leaves,
            "taken": taken_leaves,
            "balance": res,
        }
        return gen_response(200, "Leave data getting successfully", leave_applications)
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_leave_application(name):
    """
    Get Leave Application which is already applied. Get Leave Balance Report
    """
    try:
        emp_data = get_employee_by_user(frappe.session.user)
        validate_employee_data(emp_data)

        if not frappe.db.exists(
            "OTPL Leave", {"name": name, "employee": emp_data.get("name")}
        ):
            return gen_response(500, "Leave application does not exists!")

        leave_application_fields = [
            "name",
            "'NA' as 'leave_type'",
            "total_no_of_days as 'total_leave_days'",
            "reason as 'description'",
            "status",
            "half_day",
            "from_date",
            "to_date",
            "DATE_FORMAT(creation, '%%d-%%m-%%y') as 'posting_date'",
            "half_day_date",
            "alternate_mobile_no as 'alternate_mobile_number'",
            "approved_from_date",
            "approved_to_date",
            "total_no_of_approved_days"
        ]

        leave_application = frappe.db.sql(
            f"""
            SELECT {", ".join(leave_application_fields)}
            FROM `tabOTPL Leave`
            WHERE name = %s
            """,
            name,
            as_dict=True
        )
        
        if leave_application:
            leave_application = leave_application[0]
        else:
            return gen_response(500, "Leave application not found")

        return gen_response(200, "Leave data getting successfully", leave_application)
    except Exception as e:
        return exception_handler(e)


# def get_leave_balance_report(employee, company, fiscal_year):
#     fiscal_year = get_fiscal_year(fiscal_year=fiscal_year, as_dict=True)
#     year_start_date = get_date_str(fiscal_year.get("year_start_date"))
#     # year_end_date = get_date_str(fiscal_year.get("year_end_date"))
#     filters_leave_balance = {
#         "from_date": year_start_date,
#         "to_date": add_days(today(), 1),
#         "company": company,
#         "employee": employee,
#     }
#     from frappe.desk.query_report import run

#     result = run("Employee Leave Balance", filters=filters_leave_balance)
#     for row in result.get("result"):
#         frappe.log_error(title="180", message=row)
#         frappe.log_error(title="180", message=type(row.get("employee")))
#         if isinstance(row.get("employee"), tuple):
#             row["employee"] = employee
#     return result


def get_leave_balance_report(employee, company, fiscal_year):
    fiscal_year = get_fiscal_year(fiscal_year=fiscal_year, as_dict=True)
    year_start_date = get_date_str(fiscal_year.get("year_start_date"))
    year_end_date = get_date_str(fiscal_year.get("year_end_date"))
    filters_leave_balance = {
        "from_date": year_start_date,
        "to_date": year_end_date,
        "company": company,
        "employee": employee,
    }
    return leave_report(filters_leave_balance)


def leave_report(filters):
    leave_types = frappe.db.sql_list(
        "select name from `tabLeave Type` order by name asc"
    )
    return get_data(filters, leave_types)


def get_data(filters, leave_types):
    user = frappe.session.user

    if filters.get("to_date") <= filters.get("from_date"):
        frappe.throw(_("'From Date should be less than To Date"))

    data = []
    # for employee in active_employees:
    #     leave_approvers = department_approver_map.get(employee.department_name, [])
    #     if employee.leave_approver:
    #         leave_approvers.append(employee.leave_approver)

    #     if (
    #         (len(leave_approvers) and user in leave_approvers)
    #         or (user in ["Administrator", employee.user_id])
    #         or ("HR Manager" in frappe.get_roles(user))
    #     ):
    #         # row = [employee.name, employee.employee_name, employee.department]
    #         row = dict(
    #             employee=employee.name,
    #             employee_name=employee.employee_name,
    #         )

    for leave_type in leave_types:
        row = {}
        row["leave_type"] = leave_type
        row["employee"] = filters.get("employee")
        row["employee_name"] = frappe.db.get_value(
            "Employee", filters.get("employee"), "employee_name"
        )
        row.update(
            calculate_leaves_details(filters, leave_type, filters.get("employee"))
        )

        # row += calculate_leaves_details(filters, leave_type, employee)

        data.append(row)
    return data


def get_leave_ledger_entries(from_date, to_date, employee, leave_type):
    records = frappe.db.sql(
        """
		SELECT
			employee, leave_type, from_date, to_date, leaves, transaction_name, transaction_type
			is_carry_forward, is_expired
		FROM `tabLeave Ledger Entry`
		WHERE employee=%(employee)s AND leave_type=%(leave_type)s
			AND docstatus=1
			AND (from_date between %(from_date)s AND %(to_date)s
				OR to_date between %(from_date)s AND %(to_date)s
				OR (from_date < %(from_date)s AND to_date > %(to_date)s))
	""",
        {
            "from_date": from_date,
            "to_date": to_date,
            "employee": employee,
            "leave_type": leave_type,
        },
        as_dict=1,
    )

    return records


def calculate_leaves_details(filters, leave_type, employee):
    ledger_entries = get_leave_ledger_entries(
        filters.get("from_date"), filters.get("to_date"), employee, leave_type
    )

    # Leaves Deducted consist of both expired and leaves taken
    leaves_deducted = (
        get_leaves_for_period(
            employee, leave_type, filters.get("from_date"), filters.get("to_date")
        )
        * -1
    )

    # removing expired leaves
    leaves_taken = leaves_deducted - remove_expired_leave(ledger_entries)

    opening = get_leave_balance_on(
        employee, leave_type, add_days(filters.get("from_date"), -1)
    )

    new_allocation, expired_allocation = get_allocated_and_expired_leaves(
        ledger_entries, filters.get("from_date"), filters.get("to_date")
    )

    # removing leaves taken from expired_allocation
    expired_leaves = max(expired_allocation - leaves_taken, 0)

    # Formula for calculating  closing balance
    closing = max(opening + new_allocation - (leaves_taken + expired_leaves), 0)
    return dict(
        leaves_allocated=flt(new_allocation),
        leaves_expired=flt(expired_leaves),
        opening_balance=flt(opening),
        leaves_taken=flt(leaves_taken),
        closing_balance=flt(closing),
    )
    # return [opening, new_allocation, leaves_taken, expired_leaves, closing]


def remove_expired_leave(records):
    expired_within_period = 0
    for record in records:
        if record.is_expired:
            expired_within_period += record.leaves
    return expired_within_period * -1


def get_allocated_and_expired_leaves(records, from_date, to_date):

    from frappe.utils import getdate

    new_allocation = 0
    expired_leaves = 0

    for record in records:
        if record.to_date < getdate(today()) and record.leaves > 0:
            expired_leaves += record.leaves

        if record.from_date >= getdate(from_date) and record.leaves > 0:
            new_allocation += record.leaves

    return new_allocation, expired_leaves


@frappe.whitelist()
def get_expense_type():
    try:
        expense_types = frappe.get_all(
            "Expense Claim Type", filters={}, fields=["name"]
        )
        return gen_response(200, "Expense type get successfully", expense_types)
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def book_expense(*args, **kwargs):
    try:
        emp_data = get_employee_by_user(
            frappe.session.user, fields=["name", "company", "expense_approver"]
        )
        if not len(emp_data) >= 1:
            return gen_response(500, "Employee does not exists")
        validate_employee_data(emp_data)
        data = kwargs
        payable_account = get_payable_account(emp_data.get("company"))
        expense_doc = frappe.get_doc(
            dict(
                doctype="Expense Claim",
                employee=emp_data.name,
                expense_approver=emp_data.expense_approver,
                expenses=[
                    {
                        "expense_date": data.get("expense_date"),
                        "expense_type": data.get("expense_type"),
                        "description": data.get("description"),
                        "amount": data.get("amount"),
                    }
                ],
                posting_date=today(),
                company=emp_data.get("company"),
                payable_account=payable_account,
            )
        ).insert()
        # expense_doc.submit()
        if not data.get("attachments") == None:
            for file in data.get("attachments"):
                frappe.db.set_value(
                    "File", file.get("name"), "attached_to_name", expense_doc.name
                )
        return gen_response(200, "Expense applied successfully", expense_doc)
    except Exception as e:
        return exception_handler(e)


def get_payable_account(company):
    ess_settings = get_ess_settings()
    default_payable_account = ess_settings.get("default_payable_account")
    if not default_payable_account:
        default_payable_account = frappe.db.get_value(
            "Company", company, "default_payable_account"
        )
        if not default_payable_account:
            return gen_response(
                500,
                "Set Default Payable Account Either In ESS Settings or Company Settings",
            )
        else:
            return default_payable_account
    return default_payable_account


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_expense_list():
    try:
        global_defaults = get_global_defaults()
        emp_data = get_employee_by_user(frappe.session.user)
        if not len(emp_data) >= 1:
            return gen_response(500, "Employee does not exists")
        validate_employee_data(emp_data)
        expense_list = frappe.get_all(
            "Expense Claim",
            filters={"employee": emp_data.get("name")},
            fields=["*"],
        )
        expense_data = {}
        for expense in expense_list:
            (
                expense["expense_type"],
                expense["expense_description"],
                expense["expense_date"],
            ) = frappe.get_value(
                "Expense Claim Detail",
                {"parent": expense.name},
                ["expense_type", "description", "expense_date"],
            )
            expense["expense_date"] = expense["expense_date"].strftime("%d-%m-%Y")
            expense["posting_date"] = expense["posting_date"].strftime("%d-%m-%Y")
            expense["total_claimed_amount"] = fmt_money(
                expense["total_claimed_amount"],
                currency=global_defaults.get("default_currency"),
            )
            expense["attachments"] = frappe.get_all(
                "File",
                filters={
                    "attached_to_doctype": "Expense Claim",
                    "attached_to_name": expense.name,
                    "is_folder": 0,
                },
                fields=["file_url"],
            )

            month_year = get_month_year_details(expense)
            if not month_year in list(expense_data.keys())[::-1]:
                expense_data[month_year] = [expense]
            else:
                expense_data[month_year].append(expense)
        return gen_response(200, "Expense date get successfully", expense_data)
    except Exception as e:
        return exception_handler(e)


def get_month_year_details(expense):
    date = getdate(expense.get("posting_date"))
    month = date.strftime("%B")
    year = date.year
    return f"{month} {year}"


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_salary_sllip():
    try:
        emp_data = get_employee_by_user(frappe.session.user)
        if not len(emp_data) >= 1:
            return gen_response(500, "Employee does not exists")
        validate_employee_data(emp_data)
        salary_slip_list = frappe.get_all(
            "Salary Slip",
            filters={"employee": emp_data.get("name")},
            fields=["posting_date", "name"],
        )
        ss_data = []
        for ss in salary_slip_list:
            ss_details = {}
            month_year = get_month_year_details(ss)
            ss_details["month_year"] = month_year
            ss_details["salary_slip_id"] = ss.name
            ss_details["details"] = get_salary_slip_details(ss.name)
            ss_data.append(ss_details)
        return gen_response(200, "Salary slip details get successfully", ss_data)
    except Exception as e:
        return exception_handler(e)


def get_salary_slip_details(ss_id):
    return frappe.get_doc("Salary Slip", ss_id)


@frappe.whitelist()
@ess_validate(methods=["GET", "POST"])
def download_salary_slip(ss_id):
    try:
        emp_data = get_employee_by_user(frappe.session.user)
        res = frappe.get_doc("Salary Slip", ss_id)
        if not emp_data.get("name") == res.get("employee"):
            return gen_response(
                500, "Does not have persmission to read this salary slip"
            )
        default_print_format = frappe.db.get_value(
            "Employee Self Service Settings",
            "Employee Self Service Settings",
            "default_print_format",
        )
        if not default_print_format:
            default_print_format = (
                frappe.db.get_value(
                    "Property Setter",
                    dict(property="default_print_format", doc_type=res.doctype),
                    "value",
                )
                or "Standard"
            )
        language = frappe.get_system_settings("language")
        # return  frappe.utils.get_url()
        # url = f"{ frappe.utils.get_url() }/{ res.doctype }/{ res.name }?format={ default_print_format or 'Standard' }&_lang={ language }&key={ res.get_signature() }"
        # return url
        download_pdf(res.doctype, res.name, default_print_format, res)
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
def download_pdf(doctype, name, format=None, doc=None, no_letterhead=0):
    from frappe.utils.pdf import get_pdf, cleanup

    html = frappe.get_print(doctype, name, format, doc=doc, no_letterhead=no_letterhead)
    frappe.local.response.filename = "{name}.pdf".format(
        name=name.replace(" ", "-").replace("/", "-")
    )
    frappe.local.response.filecontent = get_pdf(html)
    frappe.local.response.type = "download"


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_dashboard():
    try:
        emp_data = get_employee_by_user(
            frappe.session.user,
            fields=[
                "name",
                "company",
                "image",
                "employee_name",
                "location",
                "reports_to",
                "business_vertical",
                "sales_order",
                "external_sales_order",
                "external_so",
                "external_business_vertical",
            ],
        )
        notice_board = get_notice_board(emp_data.get("name"))
        # attendance_details = get_attendance_details(emp_data)
        log_details = get_last_log_details(emp_data.get("name"))
        settings = get_ess_settings()

        dashboard_data = {
            "notice_board": notice_board,
            "leave_balance": [],
            "latest_leave": {},
            "latest_expense": {},
            "latest_salary_slip": {},
            "stop_location_validate": settings.get("location_validate"),
            "last_log_type": log_details.get("log_type"),
            "version": settings.get("version") or "1.0",
            "update_version_forcefully": settings.get("update_version_forcefully") or 1,
            "company": emp_data.get("company") or "Employee Dashboard",
            "last_log_time": (
                log_details.get("time").strftime("%I:%M%p")
                if log_details.get("time")
                else ""
            ),
            "check_in_with_image": settings.get("check_in_with_image"),
            "check_in_with_location": settings.get("check_in_with_location"),
            "quick_task": settings.get("quick_task"),
            "allow_odometer_reading_input": settings.get(
                "allow_odometer_reading_input"
            ),
            "check_in_request": 1 if emp_data.get("location") == "Site" else 0,
            "location": emp_data.get("location"),
            "business_vertical": (
                emp_data.get("business_vertical")
                if emp_data.get("external_sales_order") != 1
                else emp_data.get("external_business_vertical")
            ),
            "sales_order": (
                emp_data.get("sales_order")
                if emp_data.get("external_sales_order") != 1
                else emp_data.get("external_so")
            ),
        }

        approval_manager = emp_data.get("reports_to")
        if approval_manager:
            approval_manager = frappe.db.get_value(
                "Employee", approval_manager, "user_id"
            )
        dashboard_data["approval_manager"] = approval_manager or None
        dashboard_data["employee_image"] = emp_data.get("image")
        dashboard_data["employee_name"] = emp_data.get("employee_name")

        # Check if user has "SITE EXPENSE INITIATOR" role
        user_roles = frappe.get_roles(frappe.session.user)
        dashboard_data["allow_expense"] = (
            1 if "SITE EXPENSE INITIATOR" in user_roles else 0
        )
        if emp_data.get("location") == "Site":
            dashboard_data["allow_checkout"] = 0
            dashboard_data["allow_checkin"] = 1
        else:
            dashboard_data["allow_checkout"] = 1
            dashboard_data["allow_checkin"] = 1

        get_latest_leave(dashboard_data, emp_data.get("name"))
        get_latest_expense(dashboard_data, emp_data.get("name"))
        get_latest_ss(dashboard_data, emp_data.get("name"))
        get_last_log_type(dashboard_data, emp_data.get("name"))
        return gen_response(200, "Dashboard data get successfully", dashboard_data)

    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
def get_leave_balance_dashboard():
    try:
        emp_data = get_employee_by_user(frappe.session.user, fields=["name", "company"])
        fiscal_year = get_fiscal_year(nowdate())[0]
        dashboard_data = {"leave_balance": []}
        if fiscal_year:
            res = get_leave_balance_report(
                emp_data.get("name"), emp_data.get("company"), fiscal_year
            )
            dashboard_data["leave_balance"] = res
        return gen_response(200, "Leave balance data get successfully", dashboard_data)
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
def get_attendance_details_dashboard():
    try:
        emp_data = get_employee_by_user(frappe.session.user, fields=["name", "company"])
        attendance_details = get_attendance_details(emp_data)
        return gen_response(
            200, "Leave balance data get successfully", attendance_details
        )
    except Exception as e:
        return exception_handler(e)


def get_last_log_details(employee):
    log_details = frappe.db.sql(
        """SELECT log_type,
        time
        FROM `tabEmployee Checkin`
        WHERE employee=%s
        AND DATE(time)=%s
        ORDER BY time DESC""",
        (employee, today()),
        as_dict=1,
    )

    if log_details:
        return log_details[0]
    else:
        return {"log_type": "OUT", "time": None}


def get_notice_board(employee=None):
    filters = [
        ["Notice Board Employee", "employee", "=", employee],
        ["Notice Board", "apply_for", "=", "Specific Employees"],
        ["Notice Board", "from_date", "<=", today()],
        ["Notice Board", "to_date", ">=", today()],
    ]
    notice_board_employee = frappe.get_all(
        "Notice Board",
        filters=filters,
        fields=["notice_title as title", "message"],
    )
    common_filters = [
        ["Notice Board", "apply_for", "=", "All Employee"],
        ["Notice Board", "from_date", "<=", today()],
        ["Notice Board", "to_date", ">=", today()],
    ]
    notice_board_common = frappe.get_all(
        "Notice Board",
        filters=common_filters,
        fields=["notice_title as title", "message"],
    )
    notice_board_employee.extend(notice_board_common)
    return notice_board_employee


def get_attendance_details(emp_data, year=None, month=None):
    last_date = get_last_day(today())
    first_date = get_first_day(today())
    total_days = date_diff(last_date, first_date) + 1
    till_date_days = date_diff(today(), first_date) + 1
    days_off = 0
    absent = 0
    total_present = 0
    attendance_report = run_attendance_report(
        emp_data.get("name"), emp_data.get("company")
    )
    if attendance_report:
        days_off = flt(attendance_report.get("total_leaves")) + flt(
            attendance_report.get("total_holidays")
        )
        absent = till_date_days - (
            flt(days_off) + flt(attendance_report.get("total_present"))
        )
        total_present = attendance_report.get("total_present")
    attendance_details = {
        "month_title": f"{frappe.utils.getdate().strftime('%B')} Details",
        "data": [
            {
                "type": "Total Days",
                "data": [
                    till_date_days,
                    total_days,
                ],
            },
            {
                "type": "Presents",
                "data": [
                    total_present,
                    till_date_days,
                ],
            },
            {
                "type": "Absents",
                "data": [
                    absent,
                    till_date_days,
                ],
            },
            {
                "type": "Days off",
                "data": [
                    days_off,
                    till_date_days,
                ],
            },
        ],
    }
    return attendance_details


@frappe.whitelist()
def run_attendance_report(employee, company):
    filters = {
        "month": cstr(get_month_name(frappe.utils.getdate().month)),
        "year": cstr(frappe.utils.getdate().year),
        "company": company,
        "employee": employee,
        "summarized_view": 1,
    }
    from frappe.desk.query_report import run

    attendance_report = run("Monthly Attendance Sheet", filters=filters)
    if attendance_report.get("result"):
        return attendance_report.get("result")[0]


def get_month_name(month):
    month_list = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]
    return month_list[int(month) - 1]


def get_latest_leave(dashboard_data, employee):
    leave_applications = frappe.get_all(
        "Leave Application",
        filters={"employee": employee},
        fields=[
            "status",
            "name",
            "DATE_FORMAT(from_date, '%d-%m-%Y') AS from_date",
            "DATE_FORMAT(to_date, '%d-%m-%Y') AS to_date",
            "name",
            "leave_type",
            "description",
        ],
        order_by="modified desc",
    )
    if len(leave_applications) >= 1:
        dashboard_data["latest_leave"] = leave_applications[0]


def get_latest_expense(dashboard_data, employee):
    global_defaults = get_global_defaults()
    expense_list = frappe.get_all(
        "OTPL Expense",
        filters={"sent_by": employee},
        fields=["*"],
        order_by="modified desc",
    )
    if len(expense_list) >= 1:
        if expense_list[0]["amount"]:
            expense_list[0]["amount"] = fmt_money(
                expense_list[0].get("amount"),
                currency=global_defaults.get("default_currency"),
            )

        dashboard_data["latest_expense"] = expense_list[0]


def get_latest_ss(dashboard_data, employee):
    global_defaults = get_global_defaults()
    salary_slips = frappe.get_all(
        "Salary Slip",
        filters={"employee": employee},
        fields=["*"],
        order_by="modified desc",
    )
    if len(salary_slips) >= 1:
        month_year = get_month_year_details(salary_slips[0])
        dashboard_data["latest_salary_slip"] = dict(
            name=salary_slips[0].name,
            month_year=month_year,
            posting_date=salary_slips[0].posting_date.strftime("%d-%m-%Y"),
            # amount=salary_slips[0].gross_pay,
            amount=fmt_money(
                salary_slips[0].gross_pay,
                currency=global_defaults.get("default_currency"),
            ),
            total_working_days=salary_slips[0].total_working_days,
        )


@frappe.whitelist()
def create_employee_log(
    log_type,
    location=None,
    odometer_reading=None,
    log_time=None,
    attendance_image=None,
    requested_from=None,
    reason=None,
    today_work=None,
    order=None,
):
    try:
        if not log_time:
            log_time = now_datetime().__str__()[:-7]
        emp_data = get_employee_by_user(
            frappe.session.user,
            fields=["name", "default_shift", "sales_order", "reports_to"],
        )

        order = emp_data.get("sales_order") or None

        log_doc = frappe.get_doc(
            dict(
                doctype="Employee Checkin",
                employee=emp_data.get("name"),
                log_type=log_type,
                time=log_time,
                location=location,
                odometer_reading=odometer_reading,
                reason=reason,
                today_work=today_work,
                order=order,
                requested_from=emp_data.get("reports_to"),
            )
        ).insert(ignore_permissions=True)
        # update_shift_last_sync(emp_data)

        if "file" in frappe.request.files:
            file = upload_file()
            file.attached_to_doctype = "Employee Checkin"
            file.attached_to_name = log_doc.name
            file.attached_to_field = "attendance_image"
            file.save(ignore_permissions=True)
            log_doc.attendance_image = file.get("file_url")
            log_doc.save(ignore_permissions=True)

        # update_shift_last_sync(emp_data)
        return gen_response(200, "Employee log added")
    except Exception as e:
        return exception_handler(e)


def update_shift_last_sync(emp_data):
    if emp_data.get("default_shift"):
        frappe.db.set_value(
            "Shift Type",
            emp_data.get("default_shift"),
            "last_sync_of_checkin",
            now_datetime(),
        )


def get_last_log_type(dashboard_data, employee):
    logs = frappe.get_all(
        "Employee Checkin",
        filters={"employee": employee},
        fields=["log_type"],
        order_by="time desc",
    )

    if len(logs) >= 1:
        dashboard_data["last_log_type"] = logs[0].log_type


def daily_notice_board_event():
    create_employee_birthday_board("birthday")
    create_employee_birthday_board("work_anniversary")


def create_employee_birthday_board(event_type):
    event_type_map = {"work_anniversary": "Work Anniversary", "birthday": "Birthday"}
    title, message = frappe.db.get_value(
        "Notice Board Template",
        {"notice_board_template_type": event_type_map.get(event_type)},
        ["board_title", "message"],
    )
    if title and message:
        emp_today_birthdays = get_employees_having_an_event_today(event_type)
        for emp in emp_today_birthdays:
            doc = frappe.get_doc(
                dict(
                    doctype="Notice Board",
                    notice_title=title,
                    message=message,
                    from_date=today(),
                    to_date=today(),
                    apply_for="Specific Employees",
                    employees=[dict(employee=emp.get("emp_id"))],
                )
            ).insert(ignore_permissions=True)


def get_employees_having_an_event_today(event_type, date=None):
    if event_type == "birthday":
        condition_column = "date_of_birth"
    elif event_type == "work_anniversary":
        condition_column = "date_of_joining"
    else:
        return

    employees_born_today = frappe.db.multisql(
        {
            "mariadb": f"""
			SELECT `name` as 'emp_id',`personal_email`, `company`, `company_email`, `user_id`, `employee_name` AS 'name', `image`, `date_of_joining`
			FROM `tabEmployee`
			WHERE
				DAY({condition_column}) = DAY(%(today)s)
			AND
				MONTH({condition_column}) = MONTH(%(today)s)
			AND
				`status` = 'Active'
		""",
            "postgres": f"""
			SELECT "name" AS 'emp_id',"personal_email", "company", "company_email", "user_id", "employee_name" AS 'name', "image"
			FROM "tabEmployee"
			WHERE
				DATE_PART('day', {condition_column}) = date_part('day', %(today)s)
			AND
				DATE_PART('month', {condition_column}) = date_part('month', %(today)s)    
			AND
				"status" = 'Active'
		""",
        },
        dict(today=getdate(date), condition_column=condition_column),
        as_dict=1,
    )
    return employees_born_today


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_task_list(start=0, page_length=10, filters=None):
    try:
        frappe.log_error(title="filters", message=filters)
        tasks = frappe.get_list(
            "Task",
            fields=[
                "name",
                "subject",
                "project",
                "priority",
                "status",
                "description",
                "exp_end_date",
                "_assign as assigned_to",
                "owner as assigned_by",
                "progress",
                "issue",
            ],
            filters=filters,
            start=start,
            page_length=page_length,
            order_by="modified desc",
        )
        for task in tasks:
            # if frappe.session.user == task.get("assigned_by") or frappe.session.user == task.get("completed_by") or (task.get("assigned_to") and frappe.session.user in task.get("assigned_to")):
            if task["exp_end_date"]:
                task["exp_end_date"] = task["exp_end_date"].strftime("%d-%m-%Y")
            get_task_comments(task)
            task["project_name"] = frappe.db.get_value(
                "Project", {"name": task.get("project")}, ["project_name"]
            )
            get_task_assigned_by(task)
            if task.get("assigned_to"):
                task["assigned_to"] = frappe.get_all(
                    "User",
                    filters=[
                        ["User", "email", "in", json.loads(task.get("assigned_to"))]
                    ],
                    fields=["full_name as user", "user_image"],
                    order_by="creation asc",
                )
            else:
                task["assigned_to"] = []
                # updated_task.append(task)

        return gen_response(200, "Task list getting Successfully", tasks)
    except Exception as e:
        return exception_handler(e)


def get_task_assigned_by(task):
    task["assigned_by"] = frappe.db.get_value(
        "User",
        {"name": task.get("assigned_by")},
        ["full_name as user", "user_image"],
        as_dict=1,
    )


def get_task_comments(task):
    comments = frappe.get_all(
        "Comment",
        filters={
            "reference_name": ["like", "%{0}%".format(task.get("name"))],
            "comment_type": "Comment",
        },
        fields=[
            "content as comment",
            "comment_by",
            "reference_name",
            "creation",
            "comment_email",
        ],
    )
    for comment in comments:
        comment["commented"] = pretty_date(comment["creation"])
        comment["creation"] = comment["creation"].strftime("%I:%M %p")
        user_image = frappe.get_value(
            "User", comment.comment_email, "user_image", cache=True
        )
        comment["user_image"] = user_image

    task["comments"] = comments
    task["num_comments"] = len(comments)


def validate_assign_task(task_id):
    assigned_to = frappe.get_value(
        "Task",
        {"name": task_id},
        ["_assign", "status"],
        cache=True,
        as_dict=True,
    )

    if assigned_to.get("_assign") == None:
        frappe.throw("Task not assigned for any user")

    elif frappe.session.user not in assigned_to.get("_assign"):
        frappe.throw("You are not authorized to update this task")


@frappe.whitelist()
@ess_validate(methods=["POST"])
def update_task_status(task_id=None, new_status=None):
    try:
        if not task_id or not new_status:
            return gen_response(500, "task id and new status is required")
        validate_assign_task(task_id=task_id)
        task_doc = frappe.get_doc("Task", task_id)
        if task_doc.get("status") == new_status:
            return gen_response(500, "status already up-to-date")
        task_doc.status = new_status
        if task_doc.status == "Completed":
            task_doc.completed_by = frappe.session.user
            task_doc.completed_on = today()
        task_doc.save()
        return gen_response(200, "Task status updated successfully")

    except frappe.PermissionError:
        return gen_response(500, "Not permitted for update task")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def update_task_progress(task_id=None, progress=None):
    try:
        if not task_id or not progress:
            return gen_response(500, "task id and progress is required")
        validate_assign_task(task_id=task_id)
        if progress:
            frappe.db.set_value("Task", task_id, "progress", progress)
        return gen_response(200, "Progress updated successfully")
    except frappe.PermissionError:
        return gen_response(500, "Not permitted for update task")
    except Exception as e:
        return exception_handler(e)





@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_holiday_list(year=None):
    try:
        if not year:
            return gen_response(500, "year is required")
        emp_data = get_employee_by_user(frappe.session.user)

        from erpnext.hr.doctype.employee.employee import get_holiday_list_for_employee

        holiday_list = get_holiday_list_for_employee(
            emp_data.name, raise_exception=False
        )

        if not holiday_list:
            return gen_response(200, "Holiday list get successfully", [])

        holidays = frappe.get_all(
            "Holiday",
            filters={
                "parent": holiday_list,
                "holiday_date": ("between", [f"{year}-01-01", f"{year}-12-31"]),
            },
            fields=["description", "holiday_date"],
            order_by="holiday_date asc",
        )

        if len(holidays) == 0:
            return gen_response(500, f"no holidays found for year {year}")

        holiday_list = []

        for holiday in holidays:
            holiday_date = frappe.utils.data.getdate(holiday.holiday_date)
            holiday_list.append(
                {
                    "year": holiday_date.strftime("%Y"),
                    "date": holiday_date.strftime("%d %b"),
                    "day": holiday_date.strftime("%A"),
                    "description": holiday.description,
                }
            )
        return gen_response(200, "Holiday list get successfully", holiday_list)
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_holiday_list_v2():
    try:
        global_defaults = get_global_defaults()
        default_company = global_defaults.get("default_company")
        
        if not default_company:
            return gen_response(500, "Default company not set in Global Defaults")
        
        # Get default holiday list from company
        default_holiday_list = frappe.db.get_value(
            "Company", default_company, "default_holiday_list"
        )
        
        if not default_holiday_list:
            return gen_response(500, "Default holiday list not set for company")
        
        # Fetch all holidays from the holiday list
        holidays = frappe.get_all(
            "Holiday",
            filters={"parent": default_holiday_list},
            fields=["description", "holiday_date"],
            order_by="holiday_date asc",
        )
        
        if len(holidays) == 0:
            return gen_response(200, "Holiday list get successfully", [])
        
        holiday_list = []
        for holiday in holidays:
            holiday_date = frappe.utils.data.getdate(holiday.holiday_date)
            holiday_list.append(
                {
                    "year": holiday_date.strftime("%Y"),
                    "date": holiday_date.strftime("%d %b"),
                    "day": holiday_date.strftime("%A"),
                    "description": holiday.description,
                }
            )
        
        return gen_response(200, "Holiday list get successfully", holiday_list)
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_task_list_dashboard():
    try:
        filters = [
            ["assign_to", "like", f"%{frappe.session.user}%"],
            ["completed", "=", 0],
        ]
        timesheet_list = frappe.get_list(
            "WMS Task",
            fields=[
                "name",
                "task_title",
                "date_of_issue",
                "due_date",
                "status",
                "source",
                "mark_incomplete",
                "date_of_completion",
                "completed",
                "date_extend_request",
                "reason",
                "details",
                "assign_by",
                "assign_to",
            ],
            order_by="modified desc",
            filters=filters,
            limit=4,
        )
        return gen_response(200, "Task List getting Successfully", timesheet_list)
    except frappe.PermissionError:
        return gen_response(500, "Not permitted read WMS Task")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_attendance_list(year=None, month=None):
    try:
        if not year or not month:
            return gen_response(500, "year and month is required", [])
        emp_data = get_employee_by_user(frappe.session.user)
        present_count = 0
        absent_count = 0
        late_count = 0

        employee_attendance_list = frappe.get_all(
            "Attendance",
            filters={
                "employee": emp_data.get("name"),
                "attendance_date": [
                    "between",
                    [
                        f"{int(year)}-{int(month)}-01",
                        f"{int(year)}-{int(month)}-{calendar.monthrange(int(year), int(month))[1]}",
                    ],
                ],
            },
            fields=[
                "name",
                "DATE_FORMAT(attendance_date, '%d %W') AS attendance_date",
                "status",
                "working_hours",
                "late_entry",
            ],
        )

        if not employee_attendance_list:
            return gen_response(500, "no attendance found for this year and month", [])

        for attendance in employee_attendance_list:
            employee_checkin_details = frappe.get_all(
                "Employee Checkin",
                filters={"attendance": attendance.get("name")},
                fields=["log_type", "time_format(time, '%h:%i%p') as time"],
            )

            attendance["employee_checkin_detail"] = employee_checkin_details

            if attendance["status"] == "Present":
                present_count += 1

                if attendance["late_entry"] == 1:
                    late_count += 1

            elif attendance["status"] == "Absent":
                absent_count += 1

            del attendance["name"]
            del attendance["status"]
            del attendance["late_entry"]

        attendance_details = {
            "days_in_month": calendar.monthrange(int(year), int(month))[1],
            "present": present_count,
            "absent": absent_count,
            "late": late_count,
        }
        attendance_data = {
            "attendance_details": attendance_details,
            "attendance_list": employee_attendance_list,
        }
        return gen_response(
            200, "Attendance data getting successfully", attendance_data
        )

    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def add_comment(reference_doctype=None, reference_name=None, content=None):
    try:
        from frappe.desk.form.utils import add_comment

        comment_by = frappe.db.get_value(
            "User", frappe.session.user, "full_name", as_dict=1
        )

        add_comment(
            reference_doctype=reference_doctype,
            reference_name=reference_name,
            content=content,
            comment_email=frappe.session.user,
            comment_by=comment_by.get("full_name"),
        )
        return gen_response(200, "Comment added successfully")

    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_comments(reference_doctype=None, reference_name=None):
    """
    reference_doctype: doctype
    reference_name: docname
    """
    try:
        filters = [
            ["Comment", "reference_doctype", "=", f"{reference_doctype}"],
            ["Comment", "reference_name", "=", f"{reference_name}"],
            ["Comment", "comment_type", "=", "Comment"],
        ]
        comments = frappe.get_all(
            "Comment",
            filters=filters,
            fields=[
                "content as comment",
                "comment_by",
                "creation",
                "comment_email",
            ],
        )

        for comment in comments:
            user_image = frappe.get_value(
                "User", comment.comment_email, "user_image", cache=True
            )
            comment["user_image"] = user_image
            comment["commented"] = pretty_date(comment["creation"])
            comment["creation"] = comment["creation"].strftime("%I:%M %p")

        return gen_response(200, "Comments get successfully", comments)

    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_profile():
    try:
        emp_data = get_employee_by_user(frappe.session.user)
        employee_details = frappe.get_cached_value(
            "Employee",
            emp_data.get("name"),
            [
                "employee_name",
                "designation",
                "name",
                "date_of_joining",
                "date_of_birth",
                "gender",
                "company_email",
                "personal_email",
                "cell_number",
                "emergency_phone_number",
            ],
            as_dict=True,
        )
        employee_details["date_of_joining"] = employee_details[
            "date_of_joining"
        ].strftime("%d-%m-%Y")
        employee_details["date_of_birth"] = employee_details["date_of_birth"].strftime(
            "%d-%m-%Y"
        )

        employee_details["employee_image"] = frappe.get_cached_value(
            "Employee", emp_data.get("name"), "image"
        )

        return gen_response(200, "Profile get successfully", employee_details)
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def upload_documents():
    try:
        emp_data = get_employee_by_user(frappe.session.user)

        file_doc = upload_file()

        ess_document = frappe.get_doc(
            {
                "doctype": "ESS Documents",
                "employee_no": emp_data.get("name"),
                "title": frappe.form_dict.title,
            }
        ).insert()

        file_doc.attached_to_doctype = "ESS Documents"
        file_doc.attached_to_name = str(ess_document.name)
        file_doc.attached_to_field = "attachement"
        file_doc.save()

        ess_document.attachement = file_doc.file_url
        ess_document.save()

        return gen_response(200, "Document added successfully")
    except Exception as e:
        return exception_handler(e)

def get_file_size(file_path, unit="auto"):
    file_size = os.path.getsize(file_path)

    units = ["B", "Kb", "Mb", "Gb", "Tb"]
    if unit == "auto":
        unit_index = 0
        while file_size > 1000:
            file_size /= 1000
            unit_index += 1
            if unit_index == len(units) - 1:
                break
        unit = units[unit_index]
    else:
        unit_index = units.index(unit)

    return f"{file_size:.2f}{unit}"


@frappe.whitelist()
@ess_validate(methods=["GET"])
def document_list():
    try:
        from frappe.utils.file_manager import get_file_path

        emp_data = get_employee_by_user(frappe.session.user)
        documents = frappe.get_all(
            "ESS Documents",
            filters={
                "employee_no": emp_data.get("name"),
            },
            fields=["name", "attachement"],
        )

        if documents:
            for doc in documents:
                file = frappe.get_value(
                    "File",
                    {
                        "file_url": doc.get("attachement"),
                        "attached_to_doctype": "ESS Documents",
                        "attached_to_name": doc.get("name"),
                    },
                    ["name", "file_name", "file_size"],
                    as_dict=1,
                )
                if file:
                    doc["file_name"] = file.get("file_name")
                    doc["file_size"] = get_file_size(
                        (get_file_path(file.get("file_name"))), unit="auto"
                    )
                    doc["file_id"] = file.get("name")

            return gen_response(200, "Documents get successfully", documents)
        else:
            return gen_response(500, "No documents found for employee", [])
    except Exception as e:
        return exception_handler(e)


def leave_application_list(date=None):
    try:
        emp_data = get_employee_by_user(frappe.session.user)
        validate_employee_data(emp_data)
        leave_application_fields = [
            "name",
            "leave_type",
            "from_date",
            "to_date",
            "total_leave_days",
            "description",
            "status",
            "posting_date",
        ]

        filters = {"employee": emp_data.get("name")}

        if date:
            date = getdate(date)
            filters["from_date"] = ["<=", date]
            filters["to_date"] = [">=", date]

        upcoming_leaves = frappe.get_all(
            "Leave Application",
            filters=filters,
            fields=leave_application_fields,
        )

        leave_applications = {"upcoming": upcoming_leaves}

        return leave_applications
    except Exception as e:
        return exception_handler(e)


def notice_board_list(employee=None, date=None):
    filters = [
        ["Notice Board Employee", "employee", "=", employee],
        ["Notice Board", "apply_for", "=", "Specific Employees"],
        ["Notice Board", "from_date", "<=", getdate(date)],
        ["Notice Board", "to_date", ">=", getdate(date)],
    ]
    notice_board_employee = frappe.get_all(
        "Notice Board",
        filters=filters,
        fields=["notice_title as title", "message as description"],
    )
    common_filters = [
        ["Notice Board", "apply_for", "=", "All Employee"],
        ["Notice Board", "from_date", "<=", getdate(date)],
        ["Notice Board", "to_date", ">=", getdate(date)],
    ]
    notice_board_common = frappe.get_all(
        "Notice Board",
        filters=common_filters,
        fields=["notice_title as title", "message as description"],
    )
    notice_board_employee.extend(notice_board_common)
    return notice_board_employee


def holiday_list(date=None):
    emp_data = get_employee_by_user(frappe.session.user)
    from erpnext.hr.doctype.employee.employee import get_holiday_list_for_employee

    holiday_list = get_holiday_list_for_employee(emp_data.name, raise_exception=False)

    filters = [
        ["Holiday", "holiday_date", "=", getdate(date)],
        ["Holiday", "parent", "=", holiday_list],
    ]

    holidays = frappe.get_all(
        "Holiday", filters=filters, fields=["'holiday' as title", "description"]
    )

    return holidays


@frappe.whitelist()
@ess_validate(methods=["GET"])
def upcoming_activity(date=None):
    try:
        if not date:
            return gen_response(500, "date is required", [])

        leaves = leave_application_list(date=date)

        upcoming_data = {date: []}

        for leave in leaves["upcoming"]:
            upcoming_data[date].append(
                {"title": leave.get("name"), "description": leave.get("leave_type")}
            )

        notice_board = notice_board_list(
            get_employee_by_user(frappe.session.user).get("name"), date=date
        )
        if notice_board:
            upcoming_data[date].extend(notice_board)

        birthday = get_employees_having_an_event_today("birthday", date=date)
        for birthdate in birthday:
            upcoming_data[date].append(
                {
                    "title": f"{birthdate.get('name')}'s Birthday",
                    "description": birthdate.get("name"),
                    "image": birthdate.get("image"),
                }
            )

        work_anniversary = get_employees_having_an_event_today(
            "work_anniversary", date=date
        )
        for anniversary in work_anniversary:
            upcoming_data[date].append(
                {
                    "title": f"{anniversary.get('name')}'s work anniversary",
                    "description": anniversary.get("name"),
                    "image": anniversary.get("image"),
                }
            )
        holidays = holiday_list(date=date)
        if holidays:
            upcoming_data[date].extend(holidays)

        return gen_response(200, "Upcoming activity details", upcoming_data)

    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def employee_device_info(**kwargs):
    try:
        data = kwargs
        existing_token = frappe.db.get_value(
            "Employee Device Info",
            filters={"user": frappe.session.user},
            fieldname="name",
        )
        if frappe.db.exists("Employee Device Info", existing_token):
            token = frappe.get_doc("Employee Device Info", existing_token)
            token.platform = data.get("platform")
            token.os_version = data.get("os_version")
            token.device_name = data.get("device_name")
            token.app_version = data.get("app_version")
            token.token = data.get("token")
            token.save(ignore_permissions=True)
        else:
            token = frappe.get_doc(
                dict(
                    doctype="Employee Device Info",
                    platform=data.get("platform"),
                    os_version=data.get("os_version"),
                    device_name=data.get("device_name"),
                    app_version=data.get("app_version"),
                    token=data.get("token"),
                    user=frappe.session.user,
                )
            ).insert(ignore_permissions=True)

        emp_data = get_employee_by_user(frappe.session.user)

        existing_registration = frappe.db.get_value(
            "Employee Device Registration", {"unique_id": data.get("unique_id")}, ["name","employee"], as_dict=True
        )
        if not existing_registration:
            # Register the device if not exists
            doc = frappe.new_doc("Employee Device Registration")
            doc.employee = emp_data.get("name")
            doc.unique_id = data.get("unique_id")
            doc.insert(ignore_permissions=True)
        if existing_registration:
            if existing_registration.get("employee") != emp_data.get("name"):
                return gen_response(500, "Device already registered with another employee.")

        return gen_response(200, "Device information saved successfully!")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist(allow_guest=True)
def auto_login_with_device_token(**kwargs):
    try:
        data = kwargs
        device_registration = frappe.db.get_value(
            "Employee Device Registration",
            {"unique_id": data.get("unique_id")},
            ["employee"],
            as_dict=True,
        )
        if device_registration:
            employee_user = frappe.db.get_value(
                "Employee", device_registration.get("employee"), "user_id"
            )
            if employee_user:
                validate_employee(employee_user)
                emp_data = get_employee_by_user(employee_user)

                login_manager = LoginManager()
                login_manager.user = employee_user
                login_manager.post_login()

                if frappe.response["message"] == "Logged In":
                    frappe.response["user"] = login_manager.user
                    frappe.response["key_details"] = generate_key(login_manager.user)
                    frappe.response["employee_id"] = emp_data.get("name")
                    frappe.response["redirect_to_login"] = False
                gen_response(200, frappe.response["message"])
                return

        # Device not registered - return 200 but indicate redirect needed
        frappe.response["redirect_to_login"] = True
        return gen_response(200, "Device not registered. Please login.")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def notification_list():
    try:
        single_filters = [
            ["Push Notification", "user", "=", frappe.session.user],
            ["Push Notification", "send_for", "=", "Single User"],
        ]
        notification = frappe.get_all(
            "Push Notification",
            filters=single_filters,
            fields=["title", "message", "creation"],
        )
        multiple_filters = [
            ["Notification User", "user", "=", frappe.session.user],
            ["Push Notification", "send_for", "=", "Multiple User"],
        ]
        multiple_notification = frappe.get_all(
            "Push Notification",
            filters=multiple_filters,
            fields=["title", "message"],
        )
        notification.extend(multiple_notification)
        all_filters = [["Push Notification", "send_for", "=", "All User"]]

        all_notification = frappe.get_all(
            "Push Notification",
            filters=all_filters,
            fields=["title", "message"],
        )

        notification.extend(all_notification)

        for notified in notification:
            notified["creation"] = pretty_date(notified.get("creation"))
            notified["user_image"] = frappe.get_value(
                "User", frappe.session.user, "user_image"
            )
        return gen_response(200, "Notification list get successfully", notification)
    except Exception as e:
        return exception_handler(e)


def send_notification_on_event():
    birthday_events = get_employees_having_an_event_today("birthday", date=today())
    for event in birthday_events:
        create_push_notification(
            title=f"{event.get('name')}'s Birthday",
            message=f"Wish happy birthday to {event['name']}",
            send_for="All User",
            notification_type="event",
        )

    anniversary_events = get_employees_having_an_event_today(
        "work_anniversary", date=today()
    )
    for anniversary in anniversary_events:
        create_push_notification(
            title=f"{anniversary.get('name')}' s Work Anniversary",
            message=f"Wish work anniversary {anniversary['name']}",
            send_for="All User",
            notification_type="event",
        )


def global_holiday_list(date=None):
    global_company = frappe.db.get_single_value("Global Defaults", "default_company")
    employee_holiday_list = frappe.get_all(
        "Employee",
        {"company": global_company, "holiday_list": ("!=", "")},
        ["employee", "holiday_list", "user_id"],
    )
    holidays = []
    for employee in employee_holiday_list:
        filters = [
            ["Holiday", "holiday_date", "=", getdate(date)],
            ["Holiday", "parent", "=", employee.holiday_list],
        ]
        holidays_list = frappe.get_all(
            "Holiday", filters=filters, fields=["'holiday' as title", "description"]
        )
        for holiday in holidays_list:
            holiday["user_id"] = employee.user_id
            holidays.append(holiday)
    return holidays


def on_holiday_event():
    holiday_list = global_holiday_list(date=today())
    for holiday in holiday_list:
        create_push_notification(
            title=f"{holiday.get('title')}",
            message=f"{holiday.get('description')}",
            send_for="Single User",
            user=holiday.get("user_id"),
            notification_type="Holiday",
        )


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_manager_login_status():
    try:
        emp_data = get_employee_by_user(
            frappe.session.user, fields=["location", "reports_to"]
        )
        if not emp_data.get("location") == "Site":
            return gen_response(
                200,
                "Employee is not assigned to Site location",
                {"is_manager_logged_in": True},
            )
        if "TEAM LEADER" in frappe.get_roles(frappe.session.user):
            return gen_response(
                200, "Employee is a Team Leader", {"is_manager_logged_in": True}
            )
        if emp_data.get("location") == "Site":
            # Get reporting manager's latest check-in for today
            if not emp_data.get("external_reporting_manager") == 1:
                if not emp_data.get("reports_to"):
                    return gen_response(
                        500,
                        "Reporting manager not assigned. Please contact administrator.",
                    )
            
                latest_checkin = frappe.db.get_value(
                    "Employee Checkin",
                    {"employee": emp_data.get("reports_to"), "time": [">=", today()]},
                    ["location"],
                    order_by="time desc",
                    as_dict=1,
                )

                if not latest_checkin or not latest_checkin.get("location"):
                    manager_name = frappe.db.get_value("Employee", emp_data.get("reports_to"), "employee_name")
                    return gen_response(
                        200,
                        f"Reporting manager {manager_name} has not checked in today. Please wait for manager to check in first.",
                        {"is_manager_logged_in": False},
                    )
                else:
                    return gen_response(
                        200,
                        "Reporting manager is logged in today.",
                        {"is_manager_logged_in": True},
                    )
            else:
                if not emp_data.get("external_report_to"):
                    return gen_response(
                        500,
                        "External reporting manager not assigned. Please contact administrator.",
                    )

                latest_checkin = frappe.db.get_value(
                    "Leader Location",
                    {
                        "employee": emp_data.get("external_report_to"),
                        "datetime": [">=", today()],
                    },
                    ["location"],
                    order_by="datetime desc",
                    as_dict=1,
                )

                if not latest_checkin or not latest_checkin.get("location"):
                    manager_name = frappe.db.get_value("Employee Pull",{"employee": emp_data.get("external_report_to")}, "employee_name")
                    return gen_response(
                        200,
                        f"External reporting manager {manager_name} has not checked in today. Please wait for manager to check in first.",
                        {"is_manager_logged_in": False},
                    )
                else:
                    return gen_response(
                        200,
                        "External reporting manager is logged in today.",
                        {"is_manager_logged_in": True},
                    )
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_branch():
    try:
        emp_data = get_employee_by_user(
            frappe.session.user,
            fields=[
                "location",
                "reports_to",
                "external_reporting_manager",
                "external_report_to",
            ],
        )

        if emp_data.get("location") == "Site" and not "TEAM LEADER" in frappe.get_roles(
            frappe.session.user
        ):
            # Check if reporting manager is assigned
            if not emp_data.get("external_reporting_manager") == 1:
                if not emp_data.get("reports_to"):
                    return gen_response(
                        500,
                        "Reporting manager not assigned. Please contact administrator.",
                    )

                # Get reporting manager's latest check-in for today
                latest_checkin = frappe.db.get_value(
                    "Employee Checkin",
                    {"employee": emp_data.get("reports_to"), "time": [">=", today()]},
                    ["location"],
                    order_by="time desc",
                    as_dict=1,
                )

                if not latest_checkin or not latest_checkin.get("location"):
                    return gen_response(200, "Branch", {})
            else:
                if not emp_data.get("external_report_to"):
                    return gen_response(
                        500,
                        "External reporting manager not assigned. Please contact administrator.",
                    )

                # Get external reporting manager's latest check-in for today
                latest_checkin = frappe.db.get_value(
                    "Leader Location",
                    {
                        "employee": emp_data.get("external_report_to"),
                        "datetime": [">=", today()],
                    },
                    ["location"],
                    order_by="datetime desc",
                    as_dict=1,
                )

                if not latest_checkin or not latest_checkin.get("location"):
                    return gen_response(200, "Branch", {})

            # Parse latitude and longitude from location field (comma-separated)
            location_parts = latest_checkin.get("location").split(",")

            try:
                latitude = float(location_parts[0].strip())
                longitude = float(location_parts[1].strip())
            except (ValueError, AttributeError):
                return gen_response(
                    500,
                    "Invalid latitude/longitude values for reporting manager's check-in.",
                )

            # Return reporting manager's check-in location with radius 50
            branch = {
                "location": "Site",
                "latitude": latitude,
                "longitude": longitude,
                "radius": 50,
            }
            return gen_response(200, "Branch", branch)

        # For non-Site employees, get location from ESS Location
        branch = frappe.db.get_value(
            "ESS Location",
            {"location": emp_data.get("location")},
            ["location", "latitude", "longitude", "radius"],
            as_dict=1,
        )

        return gen_response(200, "Branch", branch)
    except Exception as e:
        return exception_handler(e)


def on_leave_application_update(doc, event):
    user = frappe.get_value("Employee", {"name": doc.employee}, "user_id")
    leave_approver = frappe.get_value(
        "Employee", {"prefered_email": doc.leave_approver}, "employee_name"
    )

    if doc.status == "Approved":
        create_push_notification(
            title=f"{doc.name} is Approved",
            message=f"{leave_approver} accept your leave request",
            send_for="Single User",
            user=user,
            notification_type="leave_application",
        )

    elif doc.status == "Rejected":
        create_push_notification(
            title=f"{doc.name} is Rejected",
            message=f"{leave_approver} reject your leave request",
            send_for="Single User",
            user=user,
            notification_type="leave_application",
        )


def on_expense_submit(doc, event):
    user = frappe.get_value("Employee", {"name": doc.employee}, "user_id")
    expense_approver = frappe.get_value(
        "Employee", {"prefered_email": doc.expense_approver}, "employee_name"
    )
    if doc.approval_status == "Approved":
        create_push_notification(
            title=f"{doc.name} is Approved",
            message=f"{expense_approver} accept your expense claim request",
            send_for="Single User",
            user=user,
            notification_type="expense_claim",
        )

    elif doc.approval_status == "Rejected":
        create_push_notification(
            title=f"{doc.name} is Rejected",
            message=f"{expense_approver} reject your expense claim request",
            send_for="Single User",
            user=user,
            notification_type="expense_claim",
        )


@frappe.whitelist()
def change_password(data):
    try:
        from frappe.utils.password import check_password, update_password

        user = frappe.session.user
        current_password = data.get("current_password")
        new_password = data.get("new_password")
        check_password(user, current_password)
        update_password(user, new_password)
        return gen_response(200, "Password updated")
    except frappe.AuthenticationError:
        return gen_response(500, "Incorrect current password")
    except Exception as e:
        return exception_handler(e)


# Need to refector this api
@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_task_by_id(task_id=None):
    try:
        if not task_id:
            return gen_response(500, "task_id is required", [])
        filters = [["Task", "name", "=", task_id]]
        tasks = frappe.db.get_value(
            "Task",
            {"name": task_id},
            [
                "name",
                "subject",
                "project",
                "priority",
                "status",
                "description",
                "exp_end_date",
                "expected_time",
                "actual_time",
                "_assign as assigned_to",
                "owner as assigned_by",
                "completed_by",
                "completed_on",
                "progress",
                "issue",
            ],
            as_dict=1,
        )
        if not tasks:
            return gen_response(500, "you have not task with this task id", [])

        tasks["assigned_by"] = frappe.db.get_value(
            "User",
            {"name": tasks.get("assigned_by")},
            ["name", "full_name as user", "full_name", "user_image"],
            as_dict=1,
        )
        tasks["completed_by"] = frappe.db.get_value(
            "User",
            {"name": tasks.get("completed_by")},
            ["name", "full_name as user", "full_name", "user_image"],
            as_dict=1,
        )
        tasks["project_name"] = frappe.db.get_value(
            "Project", {"name": tasks.get("project")}, ["project_name"]
        )

        if tasks.get("assigned_to"):
            tasks["assigned_to"] = frappe.get_all(
                "User",
                filters=[["User", "email", "in", json.loads(tasks.get("assigned_to"))]],
                fields=["name", "full_name as user", "full_name", "user_image"],
                order_by="creation asc",
            )

        comments = frappe.get_all(
            "Comment",
            filters={
                "reference_name": ["like", "%{0}%".format(tasks.get("name"))],
                "comment_type": "Comment",
            },
            fields=[
                "content as comment",
                "comment_by",
                "reference_name",
                "creation",
                "comment_email",
            ],
        )

        for comment in comments:
            comment["commented"] = pretty_date(comment["creation"])
            comment["creation"] = comment["creation"].strftime("%I:%M %p")
            comment["user_image"] = frappe.get_value(
                "User", comment.comment_email, "user_image", cache=True
            )

        tasks["comments"] = comments
        tasks["num_comments"] = len(comments)

        return gen_response(200, "Task", tasks)
    except frappe.PermissionError:
        return gen_response(500, "Not permitted read task")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def apply_expense():
    try:
        emp_data = get_employee_by_user(
            frappe.session.user, fields=["name", "company", "expense_approver"]
        )

        if not len(emp_data) >= 1:
            return gen_response(500, "Employee does not exists")
        validate_employee_data(emp_data)

        payable_account = get_payable_account(emp_data.get("company"))
        expense_doc = frappe.get_doc(
            dict(
                doctype="Expense Claim",
                employee=emp_data.name,
                expense_approver=emp_data.expense_approver,
                expenses=[
                    {
                        "expense_date": frappe.form_dict.expense_date,
                        "expense_type": frappe.form_dict.expense_type,
                        "description": frappe.form_dict.description,
                        "amount": frappe.form_dict.amount,
                    }
                ],
                posting_date=today(),
                company=emp_data.get("company"),
                payable_account=payable_account,
            )
        ).insert()

        if "file" in frappe.request.files:
            file = upload_file()
            file.attached_to_doctype = "Expense Claim"
            file.attached_to_name = expense_doc.name
            file.save(ignore_permissions=True)

        return gen_response(200, "Expense applied Successfully", expense_doc)
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def update_profile_picture():
    try:
        emp_data = get_employee_by_user(frappe.session.user)

        employee_profile_picture = upload_file()
        employee_profile_picture.attached_to_doctype = "Employee"
        employee_profile_picture.attached_to_name = emp_data.get("name")
        employee_profile_picture.attached_to_field = "image"
        employee_profile_picture.save(ignore_permissions=True)

        frappe.db.set_value(
            "Employee", emp_data.get("name"), "image", employee_profile_picture.file_url
        )
        if employee_profile_picture:
            frappe.db.set_value(
                "User",
                frappe.session.user,
                "user_image",
                employee_profile_picture.file_url,
            )
        return gen_response(200, "Employee profile picture updated successfully")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_transactions(
    from_date=None, to_date=None, party_type=None, party=None, download="false"
):
    try:
        from_date = getdate(from_date)
        to_date = getdate(to_date)
        if not from_date or not to_date:
            frappe.throw(_("Select First from date and to date"))
        global_defaults = get_global_defaults()
        if not party_type:
            party_type = "Employee"
        if not party:
            emp_data = get_employee_by_user(frappe.session.user)
            party = [emp_data.get("name")]
        allowed_party_types = ["Employee", "Customer"]

        if party_type not in allowed_party_types:
            frappe.throw(
                _("Invalid party type. Allowed party types are {0}").format(
                    ", ".join(allowed_party_types)
                )
            )
        filters_report = {
            "company": global_defaults.get("default_company"),
            "from_date": from_date,
            "to_date": to_date,
            "account": [],
            "party_type": party_type,
            "party": party,
            "group_by": "Group by Party",
            "cost_center": [],
            "project": [],
            "include_dimensions": 1,
        }
        if party_type == "Employee" and isinstance(party, list) and len(party) == 1:
            filters_report["party_name"] = frappe.db.get_value(
                party_type, party[0], "employee_name"
            )
        else:
            filters_report["party_name"] = (
                ", ".join(party) if party and len(party) > 0 else ""
            )

        from frappe.desk.query_report import run

        res = run("General Ledger", filters=filters_report, ignore_prepared_report=True)
        data = []
        total = {}
        opening_balance = {}
        if res.get("result"):
            for row in res.get("result"):
                if "gl_entry" in row.keys():
                    data.append(
                        {
                            "posting_date": row.get("posting_date").strftime(
                                "%d-%m-%Y"
                            ),
                            "voucher_type": row.get("voucher_type"),
                            "voucher_no": row.get("voucher_no"),
                            "debit": fmt_money(
                                row.get("debit"),
                                currency=global_defaults.get("default_currency"),
                            ),
                            "credit": fmt_money(
                                row.get("credit"),
                                currency=global_defaults.get("default_currency"),
                            ),
                            "balance": fmt_money(
                                row.get("balance"),
                                currency=global_defaults.get("default_currency"),
                            ),
                            "party_type": row.get("party_type"),
                            "party": row.get("party"),
                        }
                    )

                    if flt(row.get("balance")) >= 0:
                        row["color"] = "red"
                    else:
                        row["color"] = "green"
                if "'Opening'" in row.values():
                    opening_balance = {
                        "account": "Opening",
                        "posting_date": from_date.strftime("%d-%m-%Y"),
                        "credit": fmt_money(
                            row.get("credit"),
                            currency=global_defaults.get("default_currency"),
                        ),
                        "debit": fmt_money(
                            row.get("debit"),
                            currency=global_defaults.get("default_currency"),
                        ),
                        "balance": fmt_money(
                            row.get("balance"),
                            currency=global_defaults.get("default_currency"),
                        ),
                    }
                if "'Total'" in row.values():
                    total = {
                        "account": "Total",
                        "posting_date": to_date.strftime("%d-%m-%Y"),
                        "credit": fmt_money(
                            row.get("credit"),
                            currency=global_defaults.get("default_currency"),
                        ),
                        "debit": fmt_money(
                            row.get("debit"),
                            currency=global_defaults.get("default_currency"),
                        ),
                        "balance": fmt_money(
                            row.get("balance"),
                            currency=global_defaults.get("default_currency"),
                        ),
                    }
            data.insert(0, opening_balance)
            data.append(total)

            from frappe.utils.print_format import report_to_pdf

            if download == "true":
                html = frappe.render_template(
                    "employee_self_service/templates/employee_statement.html",
                    {
                        "data": data,
                        "filters": filters_report,
                        "user": frappe.db.get_value(
                            "User", frappe.session.user, "full_name"
                        ),
                    },
                    is_path=True,
                )
                return report_to_pdf(html)
        return gen_response(200, "Ledger Get Successfully", data)
    except frappe.PermissionError:
        return gen_response(500, "Not permitted general ledger report")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_customer_list(start=0, page_length=10, filters=None):
    try:
        customer = frappe.get_list(
            "Customer",
            ["name", "customer_name"],
            start=start,
            filters=filters,
            page_length=page_length,
            order_by="modified desc",
        )
        return gen_response(200, "Customr list Getting Successfully", customer)
    except frappe.PermissionError:
        return gen_response(500, "Not permitted read customer")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_employee_list(start=0, page_length=20):
    try:
        employee = frappe.get_list(
            "Employee", ["name", "employee_name"], start=start, page_length=page_length
        )
        return gen_response(200, "Employee list Getting Successfully", employee)
    except frappe.PermissionError:
        return gen_response(500, "Not permitted read employee")
    except Exception as e:
        return exception_handler(e)


def send_notification_for_task_assign(doc, event):
    from frappe.utils.data import strip_html

    if doc.status == "Open" and doc.reference_type == "Task":
        task_doc = frappe.get_doc(doc.reference_type, doc.reference_name)
        # filters = [["Task", "name", "=", f"{doc.reference_name}"]]
        # task = frappe.db.get_value(
        #     "Task", filters, ["subject", "description"], as_dict=1
        # )
        create_push_notification(
            title=f"New Task Assigned - {task_doc.get('subject')}",
            message=(
                strip_html(str(task_doc.get("description")))
                if task_doc.get("description")
                else ""
            ),
            send_for="Single User",
            user=doc.owner,
            notification_type="task_assignment",
        )


@frappe.whitelist()
@ess_validate(methods=["DELETE"])
def delete_documents(file_id=None, attached_to_name=None):
    try:
        from frappe.utils.file_manager import remove_file

        attached_to_doctype = "ESS Documents"
        remove_file(
            fid=file_id,
            attached_to_doctype=attached_to_doctype,
            attached_to_name=attached_to_name,
        )
        frappe.delete_doc(attached_to_doctype, attached_to_name, force=1)
        return gen_response(200, "you have successfully deleted ESS Document")
    except frappe.PermissionError:
        return gen_response(500, "Not permitted delete file")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def create_task(**kwargs):
    try:
        from frappe.desk.form import assign_to

        data = json.loads(frappe.request.get_data())
        task_assign_to = data.get("assign_to")
        del data["assign_to"]
        frappe.log_error(title="data", message=data)
        task_doc = frappe.new_doc("Task")
        task_doc.update(data)
        task_doc = task_doc.insert()
        frappe.log_error(title="assign", message=assign_to)
        if task_assign_to:
            assign_to.add(
                {
                    "assign_to": task_assign_to,
                    "doctype": task_doc.doctype,
                    "name": task_doc.name,
                }
            )
        return gen_response(200, "Task has been created successfully")
    except frappe.PermissionError:
        return gen_response(500, "Not permitted for create task")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def create_quick_task(**kwargs):
    try:
        from frappe.desk.form import assign_to

        data = kwargs
        task_doc = frappe.get_doc(dict(doctype="Task"))
        task_doc.update(data)
        task_doc.exp_end_date = today()
        task_doc.insert()
        assign_to.add(
            {
                "assign_to": [frappe.session.user],
                "doctype": task_doc.doctype,
                "name": task_doc.name,
            }
        )
        return gen_response(200, "Task has been created successfully")
    except frappe.PermissionError:
        return gen_response(500, "Not permitted for create task")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def get_task(**kwargs):
    try:
        data = kwargs
        task_doc = frappe.get_doc("Task", data.get("name"))
        return gen_response(200, "Task get successfully", task_doc)
    except frappe.PermissionError:
        return gen_response(500, "Not permitted for create task")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def update_task(**kwargs):
    try:
        from frappe.desk.form import assign_to

        data = kwargs
        task_doc = frappe.get_doc("Task", data.get("name"))
        if data.get("assign_to"):
            assign_to_list = data.get("assign_to")
            del data["assign_to"]

        task_doc.update(data)
        task_doc.save()
        if assign_to_list:
            if isinstance(assign_to_list, str):
                assign_to_list = [assign_to_list]

            for assign_to_user in assign_to_list:
                assign_to.add(
                    {
                        "assign_to": assign_to_user,
                        "doctype": task_doc.doctype,
                        "name": task_doc.name,
                    }
                )

        return gen_response(200, "Task has been updated successfully")
    except frappe.PermissionError:
        return gen_response(500, "Not permitted to update task")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_quick_task_list():
    try:
        tasks = frappe.get_all(
            "Task",
            fields=["name", "subject", "exp_end_date", "status"],
            filters={
                "_assign": ["like", f"%{frappe.session.user}%"],
                "exp_end_date": ["=", today()],
            },
        )
        return gen_response(200, "Task list getting Successfully", tasks)
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_project_list():
    try:
        project_list = frappe.get_list("Project", ["name", "project_name"])
        return gen_response(200, "Project List getting Successfully", project_list)
    except frappe.PermissionError:
        return gen_response(500, "Not permitted read project")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_user_list():
    try:
        user_list = frappe.get_list("User", ["name", "full_name", "user_image"])
        return gen_response(200, "User List getting Successfully", user_list)
    except frappe.PermissionError:
        return gen_response(500, "Not permitted read user")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_task_status_list():
    try:
        task_status = frappe.get_meta("Task").get_field("status").options or ""
        if task_status:
            task_status = task_status.split("\n")
        return gen_response(200, "Status get successfully", task_status)
    except Exception as e:
        return exception_handler(e)


# def send_notification_on_task_comment(doc, event):
#     from frappe.utils.data import strip_html

#     if doc.reference_doctype == "Task" and doc.comment_type == "Comment":
#         filters = [["Comment", "name", "=", f"{doc.reference_name}"]]
#         task = frappe.db.get_value(
#             "Comment", filters, ["content", "owner", "creation"], as_dict=1
#         )
#         create_push_notification(
#             title=f"New Task Comment - {task.get('owner')}",
#             message=strip_html(str(task.get("content")))
#             if task.get("content")
#             else "",
#             send_for="Multiple User",
#             user=doc.allocated_to,
#             notification_type="task_comment",
#         )


@frappe.whitelist()
def get_attendance_details_by_month(year, month):
    try:
        emp_data = get_employee_by_user(frappe.session.user, fields=["name", "company"])
        attendance_details = get_attendance_details(emp_data, year, month)
        return gen_response(
            200, "Leave balance data get successfully", attendance_details
        )
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def upload_employee_documents(document_type):
    """
    Upload employee document based on document type.
    document_type can be: Aadhar, Pan, Photograph, Cheque
    """
    try:
        emp_data = get_employee_by_user(frappe.session.user)
        
        # Map document types to employee fields
        document_field_mapping = {
            "Aadhar": "aadhar_card_document",
            "Pan": "pan_card_document",
            "Photograph": "photograph_document",
            "Cheque": "cancelled_cheque_document"
        }
        
        if document_type not in document_field_mapping:
            return gen_response(
                400, 
                f"Invalid document type. Allowed types are: {', '.join(document_field_mapping.keys())}"
            )
        
        field_name = document_field_mapping[document_type]
        
        # Check if file is present in request
        if "file" not in frappe.request.files:
            return gen_response(400, "No file uploaded")
        
        # Delete old document if exists
        old_file_url = frappe.db.get_value("Employee", emp_data.get("name"), field_name)
        if old_file_url:
            # Get the file doc and delete it
            file_doc = frappe.db.get_value("File", {"file_url": old_file_url}, "name")
            if file_doc:
                frappe.delete_doc("File", file_doc, ignore_permissions=True)
        
        # Upload new file
        uploaded_file = upload_file()
        uploaded_file.attached_to_doctype = "Employee"
        uploaded_file.attached_to_name = emp_data.get("name")
        uploaded_file.attached_to_field = field_name
        uploaded_file.save(ignore_permissions=True)
        
        # Update employee document field
        frappe.db.set_value(
            "Employee", 
            emp_data.get("name"), 
            field_name, 
            uploaded_file.file_url
        )
        
        return gen_response(
            200, 
            f"{document_type} document uploaded successfully",
            {
                "file_url": uploaded_file.file_url,
                "file_name": uploaded_file.file_name,
                "document_type": document_type
            }
        )
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_employee_documents():
    """
    Get all employee documents
    """
    try:
        emp_data = get_employee_by_user(frappe.session.user)
        
        employee = frappe.get_doc("Employee", emp_data.get("name"))
        
        documents = {
            "Aadhar": {
                "file_url": employee.aadhar_card_document or None,
                "file_name": None
            },
            "Pan": {
                "file_url": employee.pan_card_document or None,
                "file_name": None
            },
            "Photograph": {
                "file_url": employee.photograph_document or None,
                "file_name": None
            },
            "Cheque": {
                "file_url": employee.cancelled_cheque_document or None,
                "file_name": None
            }
        }
        
        # Get file names for each document
        for doc_type, doc_data in documents.items():
            if doc_data["file_url"]:
                file_name = frappe.db.get_value(
                    "File", 
                    {"file_url": doc_data["file_url"]}, 
                    "file_name"
                )
                doc_data["file_name"] = file_name
        
        return gen_response(
            200, 
            "Employee documents retrieved successfully",
            documents
        )
    except Exception as e:
        return exception_handler(e)
