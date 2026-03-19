import frappe
import requests


@frappe.whitelist()
def get_map_markers():
    """Fetch employee check-in locations from both ERP servers and return combined markers."""
    api_urls = [
        "https://erp.oberoithermit.com/api/method/locations",
        "https://erp.tranzrail.co.in/api/method/locations",
    ]

    markers = []
    for url in api_urls:
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            records = data.get("message", [])
            source_label = "Oberoi" if "oberoithermit" in url else "Tranzrail"

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
                })
        except Exception:
            frappe.log_error(
                title=f"India Map - Failed to fetch from {url}",
                message=frappe.get_traceback(),
            )

    return markers
