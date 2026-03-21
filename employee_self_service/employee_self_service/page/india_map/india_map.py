import frappe
import requests


@frappe.whitelist()
def get_map_markers(date=None):
    """Fetch employee check-in locations from both ERP servers and return combined data.

    Returns a dict:
        markers          – list of check-in records with location + extra fields
        total_active_employees – active headcount across OTPL + TRANZ
        employee_list    – all active employees (for search dropdown)
    """
    if not date:
        date = frappe.utils.today()

    api_urls = [
        "https://erp.oberoithermit.com/api/method/employee_self_service.api.locations",
        "https://erp.tranzrail.co.in/api/method/employee_self_service.api.locations",
    ]

    markers = []
    total_active = 0
    employee_list = []

    for url in api_urls:
        try:
            resp = requests.get(url, params={"date": date}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            message = data.get("message", {})
            source_label = "Oberoi" if "oberoithermit" in url else "Tranzrail"

            # Support old format (plain list) and new format (dict with metadata)
            if isinstance(message, list):
                records = message
                active_count = 0
                emp_list = []
            else:
                records = message.get("records", message.get("checkins", []))
                active_count = message.get("total_active_employees", 0)
                emp_list = message.get("employee_list", [])

            total_active += active_count

            for emp in emp_list:
                employee_list.append({
                    "employee": emp.get("employee", ""),
                    "employee_name": emp.get("employee_name", ""),
                    "company": emp.get("company", source_label),
                })

            for record in records:
                location = record.get("location", "")
                if not location or "," not in location:
                    continue

                parts = location.split(",")
                try:
                    lat = float(parts[0].strip())
                    lng = float(parts[1].strip())
                except (ValueError, IndexError):
                    continue

                markers.append({
                    "latitude": lat,
                    "longitude": lng,
                    "employee": record.get("employee", ""),
                    "employee_name": record.get("employee_name", ""),
                    "time": record.get("time", ""),
                    "source": source_label,
                    "company": record.get("company", source_label),
                    "address": record.get("address", ""),
                    "business_vertical": record.get("business_vertical", ""),
                    "sales_order": record.get("sales_order", ""),
                })
        except Exception:
            frappe.log_error(
                title=f"India Map - Failed to fetch from {url}",
                message=frappe.get_traceback(),
            )

    return {
        "markers": markers,
        "total_active_employees": total_active,
        "employee_list": employee_list,
    }
