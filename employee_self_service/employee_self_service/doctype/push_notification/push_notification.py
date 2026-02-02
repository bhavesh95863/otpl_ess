# Copyright (c) 2022, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
import json
import datetime


class PushNotification(Document):
    def after_insert(self):
        if self.send_for == "Single User":
            token = frappe.db.get_value(
                "Employee Device Info",
                filters={"user": self.user},
                fieldname="token",
            )
            if token:
                send_single_notification(
                    token,
                    self.title,
                    self.message,
                    self.user,
                    self.notification_type,
                )

        elif self.send_for == "Multiple User":
            users = [nu.user for nu in self.users]
            device_infos = frappe.db.get_list(
                "Employee Device Info",
                filters=[
                    ["Employee Device Info", "user", "in", users],
                    ["Employee Device Info", "token", "is", "set"],
                ],
                fields=["token", "user"],
            )
            if device_infos:
                send_multiple_notification(
                    device_infos,
                    self.title,
                    self.message,
                    self.notification_type,
                )

        elif self.send_for == "All User":
            device_infos = frappe.db.get_list(
                "Employee Device Info",
                filters=[["Employee Device Info", "token", "is", "set"]],
                fields=["token", "user"],
            )
            if device_infos:
                send_multiple_notification(
                    device_infos,
                    self.title,
                    self.message,
                    self.notification_type,
                )

        elif self.send_for == "Send to Group":
            group_users = frappe.get_all("Employee Group Employees",filters={"parent":self.employee_group,"parenttype":"OTPL Employee Group"},fields=["user"]) or []
            users = [gu.user for gu in group_users]
            device_infos = frappe.db.get_list(
                "Employee Device Info",
                filters=[
                    ["Employee Device Info", "user", "in", users],
                    ["Employee Device Info", "token", "is", "set"],
                ],
                fields=["token", "user"],
            )
            if device_infos:
                send_multiple_notification(
                    device_infos,
                    self.title,
                    self.message,
                    self.notification_type,
                )


@frappe.whitelist()
def send_single_notification(
    registration_id,
    title=None,
    message=None,
    user=None,
    notification_type=None,
    reference_document=None,
    reference_name=None,
    other_info=None,
):
    """Create ESS Notification Log for single user"""
    try:
        notification_log = frappe.new_doc("ESS Notification Log")
        notification_log.notification_name = title
        notification_log.subject = title
        notification_log.message = message
        notification_log.recipient = user
        notification_log.token = registration_id
        notification_log.document_type = notification_type
        notification_log.reference_document = reference_document
        notification_log.reference_name = reference_name
        notification_log.other_info = other_info
        notification_log.insert(ignore_permissions=True)
        frappe.db.commit()
        return {"success": True, "message": "Notification log created"}
    except Exception as e:
        frappe.log_error(
            title="ESS Notification Log Creation Error",
            message=frappe.get_traceback()
        )
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def send_multiple_notification(
    device_infos, title=None, message=None, notification_type=None,
    reference_document=None, reference_name=None, other_info=None
):
    """Create ESS Notification Log for multiple users"""
    try:
        for device_info in device_infos:
            notification_log = frappe.new_doc("ESS Notification Log")
            notification_log.notification_name = title
            notification_log.subject = title
            notification_log.message = message
            notification_log.recipient = device_info.get("user")
            notification_log.token = device_info.get("token")
            notification_log.document_type = notification_type
            notification_log.reference_document = reference_document
            notification_log.reference_name = reference_name
            notification_log.other_info = other_info
            notification_log.insert(ignore_permissions=True)

        frappe.db.commit()
        return {"success": True, "message": f"{len(device_infos)} notification logs created"}
    except Exception as e:
        frappe.log_error(
            title="ESS Notification Log Creation Error",
            message=frappe.get_traceback()
        )
        return {"success": False, "error": str(e)}


def create_push_notification(title, message, send_for, notification_type, user=None):
    push_notification_doc = frappe.new_doc("Push Notification")
    push_notification_doc.title = title
    push_notification_doc.message = message
    push_notification_doc.send_for = send_for
    push_notification_doc.user = user
    push_notification_doc.notification_type = notification_type
    push_notification_doc.save(ignore_permissions=True)
