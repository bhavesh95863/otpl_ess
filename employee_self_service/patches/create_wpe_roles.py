import frappe


def execute():
    """Create WPE User, WPE Manager, and WPE Admin roles if they don't exist."""
    roles = ["WPE User", "WPE Manager", "WPE Admin"]

    for role_name in roles:
        if not frappe.db.exists("Role", role_name):
            role = frappe.get_doc({
                "doctype": "Role",
                "role_name": role_name,
                "desk_access": 1,
                "is_custom": 1,
            })
            role.insert(ignore_permissions=True)
            print(f"Created role: {role_name}")
        else:
            print(f"Role already exists: {role_name}")

    frappe.db.commit()
