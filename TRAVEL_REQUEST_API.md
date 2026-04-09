# Travel Request API Documentation

Base URL: `/api/method/employee_self_service.mobile.v1`

All endpoints require authentication via API key/secret token header:
```
Authorization: token <api_key>:<api_secret>
```

---

## 1. Create Travel Request

**POST** `/travel.travel.create_travel_request`

Creates a new Travel Request or updates an existing one. Supports file upload for ticket attachment.

**Request Body** (form-data or JSON):

| Field | Type | Required | Description |
|---|---|---|---|
| `date_of_departure` | Date | Yes | Departure date (YYYY-MM-DD) |
| `date_of_arrival` | Date | Yes | Arrival date (YYYY-MM-DD) |
| `purpose` | String | Yes | One of: `Going on Leave`, `Going back to work`, `Going for official work` |
| `ticket` | Attach | Yes | Ticket attachment (can also be sent as `file` in multipart form-data) |
| `remarks` | String | No | Additional remarks |
| `name` | String | No | Pass existing Travel Request name to update instead of create |

> `employee`, `employee_name`, `department`, `report_to`, `number_of_days` are auto-set.

**Response (200):**
```json
{
  "message": "Travel Request created successfully",
  "data": { "name": "TRVL00001" }
}
```

---

## 2. Get Travel Request List

**GET** `/travel.travel.get_travel_request_list`

Lists travel requests for the logged-in employee.

**Query Parameters:**

| Param | Type | Default | Description |
|---|---|---|---|
| `start` | Int | 0 | Pagination offset |
| `page_length` | Int | 10 | Number of records per page |
| `filters` | JSON | `[]` | Additional filters (array format) |

**Response (200):**
```json
{
  "message": "Travel Request list retrieved successfully",
  "data": [
    {
      "name": "TRVL00001",
      "employee": "HR-EMP-00001",
      "employee_name": "John Doe",
      "department": "Operations",
      "date_of_departure": "2026-04-10",
      "date_of_arrival": "2026-04-15",
      "number_of_days": 6,
      "purpose": "Going on Leave",
      "status": "Pending",
      "report_to": "HR-EMP-00050",
      "ticket": "/files/ticket.pdf",
      "remarks": "",
      "creation": "2026-04-08 10:30:00"
    }
  ]
}
```

---

## 3. Get Travel Request Details

**GET** `/travel.travel.get_travel_request_details`

Returns full details of a single travel request.

**Query Parameters:**

| Param | Type | Required | Description |
|---|---|---|---|
| `name` | String | Yes | Travel Request ID (e.g. `TRVL00001`) |

**Response (200):**
```json
{
  "message": "Travel Request retrieved successfully",
  "data": {
    "name": "TRVL00001",
    "employee": "HR-EMP-00001",
    "employee_name": "John Doe",
    "department": "Operations",
    "date_of_departure": "2026-04-10",
    "date_of_arrival": "2026-04-15",
    "number_of_days": 6,
    "purpose": "Going on Leave",
    "ticket": "/files/ticket.pdf",
    "status": "Pending",
    "report_to": "HR-EMP-00050",
    "has_external_report_to": 0,
    "external_report_to": "",
    "remarks": ""
  }
}
```

---

## 4. Get Travel Purpose List

**GET** `/travel.travel.get_travel_purpose_list`

Returns the available travel purpose options.

**Response (200):**
```json
{
  "message": "Travel purpose list retrieved successfully",
  "data": [
    { "name": "Going on Leave" },
    { "name": "Going back to work" },
    { "name": "Going for official work" }
  ]
}
```

---

## 5. Get Travel Approval List (Pending)

**GET** `/approvals.otpl_approval.get_travel_approval_list`

Returns pending travel requests for the current user to approve. Combines local Travel Request (where `report_to` is the user's employee) and Travel Request Pull (from synced ERPs).

**Query Parameters:**

| Param | Type | Default | Description |
|---|---|---|---|
| `start` | Int | 0 | Pagination offset |
| `page_length` | Int | 10 | Number of records per page |

**Response (200):**
```json
{
  "message": "Travel approval list retrieved successfully",
  "data": [
    {
      "name": "TRVL00001",
      "employee": "HR-EMP-00001",
      "employee_name": "John Doe",
      "department": "Operations",
      "date_of_departure": "2026-04-10",
      "date_of_arrival": "2026-04-15",
      "number_of_days": 6,
      "purpose": "Going on Leave",
      "ticket": "/files/ticket.pdf",
      "status": "Pending",
      "report_to": "HR-EMP-00050",
      "remarks": ""
    }
  ]
}
```

---

## 6. Approve Travel Request

**POST** `/approvals.otpl_approval.approve_travel_request`

Approves a Travel Request or Travel Request Pull. Auto-detects which doctype the record belongs to.

**Request Body (JSON):**
```json
{
  "name": "TRVL00001",
  "remarks": "Approved for travel"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | String | Yes | Travel Request or Travel Request Pull ID |
| `remarks` | String | No | Approval remarks |

**Response (200):**
```json
{
  "message": "Travel Request approved successfully"
}
```

---

## 7. Reject Travel Request

**POST** `/approvals.otpl_approval.reject_travel_request`

Rejects a Travel Request or Travel Request Pull. Auto-detects which doctype.

**Request Body (JSON):**
```json
{
  "name": "TRVL00001",
  "remarks": "Dates conflict with project deadline"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | String | Yes | Travel Request or Travel Request Pull ID |
| `remarks` | String | No | Rejection reason |

**Response (200):**
```json
{
  "message": "Travel Request rejected successfully"
}
```

---

## 8. Get Approved Travel List

**GET** `/approvals.otpl_approval.get_travel_approved_list`

Returns approved travel requests for the current approver. Combines both doctypes.

**Query Parameters:**

| Param | Type | Default | Description |
|---|---|---|---|
| `start` | Int | 0 | Pagination offset |
| `page_length` | Int | 10 | Number of records per page |

**Response (200):**
```json
{
  "message": "Approved travel list retrieved successfully",
  "data": [...]
}
```

---

## 9. Pending Approval Counts (Updated)

**GET** `/approvals.otpl_approval.get_pending_approval_counts`

Now includes `travel` count in the response.

**Response (200):**
```json
{
  "message": "Pending approval counts retrieved successfully",
  "data": {
    "leave": 2,
    "expense": 1,
    "checkin": 0,
    "checkout": 0,
    "site_expense_pending": 0,
    "travel": 3,
    "total": 6
  }
}
```

---

## Error Responses

All endpoints return the same error format:

```json
{
  "message": "Error description",
  "data": []
}
```

| Status | Meaning |
|---|---|
| 200 | Success |
| 500 | Validation error, permission error, or server error |

---

## Status Flow

```
Pending → Approved
Pending → Rejected
```

- Employee creates with status `Pending` (auto-set)
- Approver (report_to from Business Line) can Approve or Reject
- After Approved/Rejected, the document becomes read-only
- Daily cron job uses dates to update employee availability/travelling fields
