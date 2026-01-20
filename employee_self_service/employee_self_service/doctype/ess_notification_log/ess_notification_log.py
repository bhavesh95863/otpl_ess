# Copyright (c) 2024, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

import json

import frappe
import requests
from frappe.model.document import Document


class ESSNotificationLog(Document):
    def after_insert(self):
        target_site_url = "https://notification.nesscale.com/api/method/ncs_nesscale.api.send_push_notification"

        erp_url = frappe.utils.get_url()

        # Prepare the payload
        payload = {
            "product_name": "OTPL ESS",
            "subject": self.subject,
            "message": self.message,
            "notification_type": "info",
            "tokens": [self.token],
            "erp_url": erp_url,
            "reference_document": self.reference_document,
            "reference_name": self.reference_name,
            "other_info": self.other_info,
        }
        # Set your headers for authentication (API key and secret)
        headers = {"Content-Type": "application/json"}

        try:
            # Send the POST request
            response = requests.post(
                target_site_url, headers=headers, data=json.dumps(payload)
            )
            # Check the response
            if response.status_code == 200:
                # Notification sent successfully
                pass
            else:
                frappe.log_error(
                    title="ESS Push Notification Error",
                    message=f"Failed to send notification. Status Code: {response.status_code}, Response: {response.text}",
                )
        except Exception:
            frappe.log_error(
                title="ESS Push Notification Error", message=frappe.get_traceback()
            )


def create_ess_notification_log(
    user,
    title,
    message,
    notification_type=None,
    reference_document=None,
    reference_name=None,
    other_info=None,
):
    """Helper function to create ESS Notification Log for a user"""
    try:
        # Get user's device token
        token = frappe.db.get_value(
            "Employee Device Info",
            filters={"user": user},
            fieldname="token",
        )
        
        if not token:
            frappe.log_error(
                title="ESS Notification Log - No Token",
                message=f"No device token found for user: {user}"
            )
            return None
        
        notification_log = frappe.new_doc("ESS Notification Log")
        notification_log.notification_name = title
        notification_log.subject = title
        notification_log.message = message
        notification_log.recipient = user
        notification_log.token = token
        notification_log.document_type = notification_type
        notification_log.reference_document = reference_document
        notification_log.reference_name = reference_name
        notification_log.other_info = other_info
        notification_log.insert(ignore_permissions=True)
        frappe.db.commit()
        
        return notification_log.name
    except Exception as e:
        frappe.log_error(
            title="ESS Notification Log Creation Error",
            message=frappe.get_traceback()
        )
        return None

