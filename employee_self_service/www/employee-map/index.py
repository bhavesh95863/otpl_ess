import frappe

no_cache = 1


def get_context(context):
    """Serve the India Map page — requires login."""
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/employee-map"
        raise frappe.Redirect

    context.no_cache = 1
    context.show_sidebar = False
