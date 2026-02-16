import frappe
from frappe import _
from employee_self_service.mobile.v1.api_utils import (
    gen_response,
    ess_validate,
    exception_handler,
)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_send_for_options():
    try:
        doctype_meta = frappe.get_meta("Push Notification")
        field_meta = doctype_meta.get_field("send_for")
        options = []

        if not field_meta:
            return gen_response(
                404, f"Field send_for not found in Push Notification", []
            )

        if field_meta and field_meta.options:
            options = [
                opt.strip()
                for opt in field_meta.options.split("\n")
                if opt.strip()
            ]

        return gen_response(
            200, "Send for options retrieved successfully", options
        )
    except frappe.PermissionError:
        return gen_response(500, "Not permitted read Push Notification")
    except Exception as e:
        return exception_handler(e)

@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_employee_group_list():
    try:
        employee_group_list = frappe.get_list("OTPL Employee Group", fields=["name"])
        return gen_response(200, "Employee Group List getting Successfully", employee_group_list)
    except frappe.PermissionError:
        return gen_response(500, "Not permitted read Employee Group")
    except Exception as e:
        return exception_handler(e)

@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_push_notification_list():
    try:
        push_notification_list = frappe.get_list("Push Notification",fields=["*"])
        return gen_response(200, "Push Notification List getting Successfully", push_notification_list)
    except frappe.PermissionError:
        return gen_response(500, "Not permitted read Push Notification")
    except Exception as e:
        return exception_handler(e)

@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_push_notification(notification_id: str):
    if not notification_id:
        frappe.throw(_("notification_id is required"))

    if not frappe.db.exists("Push Notification", notification_id):
        frappe.throw(_("Push Notification not found"))

    try:
        push_notification = frappe.get_doc(
            "Push Notification",
            notification_id
        )

        return gen_response(
            200,
            "Push Notification fetched successfully",
            push_notification
        )

    except frappe.PermissionError:
        return gen_response(
            403,
            "You are not permitted to read Push Notification"
        )

    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def create_push_notification(**data):
    try:
        if not data.get("title") or not data.get("message"):
            frappe.throw(_("Both title and message are required"))

        users = data.pop("users", [])

        # Normalize users
        if isinstance(users, str):
            users = [users]

        doc = frappe.get_doc({
            "doctype": "Push Notification",
            **data
        })

        for idx, user in enumerate(users):
            # Handle dict input
            if isinstance(user, dict):
                user = user.get("user")

            # Hard validation
            if not user or not frappe.db.exists("User", user):
                frappe.throw(
                    _("Invalid User in row #{0}: {1}").format(idx, user)
                )

            doc.append("users", {"user": user})

        doc.insert()

        return gen_response(
            200,
            "Push Notification created successfully",
            doc
        )

    except frappe.PermissionError:
        return gen_response(
            403,
            "You are not permitted to create Push Notification"
        )

    except Exception as e:
        return exception_handler(e)