import frappe
from frappe import _

def notification_log(
    notification_name,
    doctype,
    subject,
    message,
    recipient,
    token,
    reference_doctype=None,
    reference_name=None,
    other_info=None,
):
    if frappe.session.user == recipient:
        return
    notification_log = frappe.new_doc("ESS Notification Log")
    notification_log.notification_name = notification_name
    notification_log.document_type = doctype
    notification_log.subject = subject
    notification_log.message = message
    notification_log.recipient = recipient
    notification_log.token = token
    notification_log.reference_document = reference_doctype
    notification_log.reference_name = reference_name
    notification_log.other_info = other_info
    notification_log.insert(ignore_permissions=True)
