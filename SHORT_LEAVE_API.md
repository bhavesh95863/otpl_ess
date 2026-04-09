# Short Leave - Mobile API Documentation

Base URL: `/api/method/employee_self_service.mobile.v1`

All endpoints require authentication via API key/secret token header:
```
Authorization: token <api_key>:<api_secret>
```

---

## Overview

A new **"Short Leave"** feature has been added to OTPL Leave. Key rules:

- Maximum **2 Short Leaves per employee per month**
- Short Leave is always a **single day** (from_date = to_date)
- Short Leave does **NOT** consume Casual Leave or Leave Without Pay
- When `short_leave = 1`, `half_day` is automatically disabled
- Short Leave allocation must be done manually via Leave Allocation

---

## 1. Create Leave Application (with Short Leave)

**POST** `/ess.make_leave_application`

Creates a new OTPL Leave. Pass `short_leave: 1` to create a Short Leave.

**Request Body (JSON):**

| Field | Type | Required | Description |
|---|---|---|---|
| `from_date` | Date | Yes | Leave date (YYYY-MM-DD). For short leave, this is the only date needed |
| `to_date` | Date | Yes | End date (YYYY-MM-DD). For short leave, must equal `from_date` (auto-set by server) |
| `short_leave` | Int (0/1) | No | `1` = Short Leave, `0` = Regular Leave (default: `0`) |
| `half_day` | Int (0/1) | No | `1` = Half Day. **Ignored when `short_leave = 1`** |
| `half_day_date` | Date | No | Half day date. **Ignored when `short_leave = 1`** |
| `reason` | String | No | Reason for leave |
| `alternate_mobile_no` | String | No | Alternate mobile number |

> `employee`, `approver`, `status` (Pending) are auto-set by the server.

**Example - Short Leave Request:**
```json
{
  "from_date": "2026-04-15",
  "to_date": "2026-04-15",
  "short_leave": 1,
  "reason": "Personal work"
}
```

**Example - Regular Leave Request:**
```json
{
  "from_date": "2026-04-15",
  "to_date": "2026-04-17",
  "short_leave": 0,
  "reason": "Family function"
}
```

**Success Response:**
```json
{
  "status_code": 200,
  "message": "Leave application successfully added!"
}
```

**Error Response (Short Leave limit exceeded):**
```json
{
  "status_code": 500,
  "message": "Maximum 2 Short Leaves are allowed per month. Employee already has 2 Short Leave(s) in April 2026."
}
```

---

## 2. Get Leave Application List

**GET** `/ess.get_leave_application_list`

Returns upcoming and taken leaves with leave balance. Now includes `short_leave` field.

**Response:**
```json
{
  "status_code": 200,
  "message": "Leave data getting successfully",
  "data": {
    "upcoming": [
      {
        "name": "LEAVE.00045",
        "leave_type": "NA",
        "from_date": "15-04-2026",
        "to_date": "15-04-2026",
        "total_leave_days": 1,
        "description": "Personal work",
        "status": "Pending",
        "posting_date": "09-04-2026",
        "short_leave": 1
      }
    ],
    "taken": [],
    "balance": []
  }
}
```

**New field in response:**

| Field | Type | Description |
|---|---|---|
| `short_leave` | Int (0/1) | `1` if this is a Short Leave, `0` otherwise |

---

## 3. Get Single Leave Application

**GET** `/ess.get_leave_application?name=LEAVE.00045`

Returns details of a single leave application. Now includes `short_leave` field.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `name` | String | Yes | OTPL Leave document name |

**Response:**
```json
{
  "status_code": 200,
  "message": "Leave data getting successfully",
  "data": {
    "name": "LEAVE.00045",
    "leave_type": "NA",
    "total_leave_days": 1,
    "description": "Personal work",
    "status": "Approved",
    "half_day": 0,
    "short_leave": 1,
    "from_date": "2026-04-15",
    "to_date": "2026-04-15",
    "posting_date": "09-04-26",
    "half_day_date": null,
    "alternate_mobile_number": null,
    "approved_from_date": "2026-04-15",
    "approved_to_date": "2026-04-15",
    "total_no_of_approved_days": 1,
    "half_day_period": null
  }
}
```

---

## 4. Approval List (Manager)

**GET** `/approvals.otpl_approval.get_otpl_leave_approval_list`

Returns pending OTPL Leaves for approval. Now includes `short_leave` field.

**Response item:**
```json
{
  "name": "LEAVE.00045",
  "employee": "HR-EMP-00001",
  "employee_name": "John Doe",
  "from_date": "2026-04-15",
  "to_date": "2026-04-15",
  "total_no_of_days": 1,
  "half_day": 0,
  "short_leave": 1,
  "half_day_date": null,
  "alternate_mobile_no": null,
  "reason": "Personal work",
  "status": "Pending",
  "half_day_period": null
}
```

---

## 5. Approved List (Manager)

**GET** `/approvals.otpl_approval.get_otpl_leave_approved_list`

Returns approved OTPL Leaves. Now includes `short_leave` field.

Same structure as approval list above with `status: "Approved"` and additional fields `approved_from_date`, `approved_to_date`.

---

## 6. Approve Leave (Manager - No Change)

**POST** `/approvals.otpl_approval.approve_otpl_leave`

No changes to this API. Works the same for both regular and short leaves.

**Request Body:**
```json
{
  "name": "LEAVE.00045",
  "approved_from_date": "2026-04-15",
  "approved_to_date": "2026-04-15"
}
```

> For Short Leave, `approved_from_date` and `approved_to_date` should be the same date.

---

## UI Guidelines for Mobile App

1. **Leave creation form:** Add a "Short Leave" toggle/checkbox
2. **When Short Leave is ON:**
   - Hide `half_day` and `half_day_date` fields
   - Set `to_date = from_date` (lock to single day)
   - Show label like "Short Leave"
3. **When Short Leave is OFF:** Show normal leave form (existing behavior)
4. **Leave list:** Show a badge/tag "Short Leave" when `short_leave == 1`
5. **Approval screen:** Show "Short Leave" indicator so manager knows it's a short leave
