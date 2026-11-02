# OTPL Leave End Reminder — Mobile Integration Guide

Contract for the **mobile app** to show a reminder to **every employee** on the
**last day of an Approved leave**, telling them to resume work the next day. The employee answers with **Back to Work**, **Apply New Leave** or **Remind
Later**, and the answer is saved as a comment on the leave. The backend is in the
`employee_self_service` app. The app only calls the endpoints below and renders
what they return.

---

## 1. When the reminder applies

The server returns `show_reminder = 1` only when **all** of these are true:

- They have an **Approved** OTPL Leave whose **approved To Date is the requested
  date** (today by default).
- That leave is a **full-day** leave (not Short Leave, not Half Day).
- No other Approved leave **covers the next day**. If leaves run back to back,
  the leave has not really ended, so there is no reminder.
- The employee has **not** already answered **Back to Work** or **Apply New
  Leave** for that leave. **Remind Later** does not stop the reminder.

In every other case `show_reminder = 0`. The app needs **no** extra checks of its
own (status, dates): just read `show_reminder`.

---

## 2. Response envelope

`{ "http_status_code": 200|500, "message": "...", "data": <payload> }`: 200 means
success, 500 means an error (show `message`). It uses the same ESS mobile auth as
the other `mobile.v1.*` calls.

---

## 3. Endpoints

Method path prefix: `employee_self_service.mobile.v1.leave_reminder.`

### 3.1 `get_leave_end_reminder` (GET)

**Method path:**
`employee_self_service.mobile.v1.leave_reminder.get_leave_end_reminder`

**Query params (optional):**

| Param  | Type         | Default      | Notes                                   |
|--------|--------------|--------------|-----------------------------------------|
| `date` | `YYYY-MM-DD` | server today | Day to check. Omit it in production; use it only for QA. |

**Response `data` when there is a reminder:**
```json
{
  "show_reminder": 1,
  "date": "2026-11-20",
  "title": "Your leave ends today",
  "message": "Your approved leave ends today (20-11-2026). Please resume work on 21-11-2026.",
  "leaves": [
    {
      "name": "LEAVE01348",
      "from_date": "2026-11-05",
      "to_date": "2026-11-20",
      "number_of_days": 16.0,
      "reason": "Ghar pe shadi h",
      "rejoin_date": "2026-11-21"
    }
  ],
  "actions": ["Back to Work", "Apply New Leave", "Remind Later"]
}
```

**Response `data` when there is no reminder:**
```json
{ "show_reminder": 0, "date": "2026-11-05", "leaves": [] }
```
`title`, `message` and `actions` are **absent** when `show_reminder = 0`.

**Field notes**
- `date`: the day that was checked (`YYYY-MM-DD`).
- `title` / `message`: ready-to-display text built on the server. Show them as
  they are.
- `leaves`: usually one item. The list form allows for the rare case of two
  leaves ending on the same day.
  - `name`: OTPL Leave ID. Use it to open the leave detail screen.
  - `from_date` / `to_date`: the **approved** range (`YYYY-MM-DD`).
  - `number_of_days`: approved days (a float).
  - `rejoin_date`: the day after `to_date`, when the employee should be back.
- `actions`: the buttons to show, in this order. Send the tapped label back
  exactly as given (see 3.2).

**Errors (500):** `"Employee does not exist"` when the logged-in user is not
linked to an Employee. Any other server error returns its message.

### 3.2 `respond_leave_end_reminder` (POST)

Records the button the employee tapped. The server adds a comment on the OTPL
Leave (by the employee's user) so HR and the manager can see it on the leave.

**Request body:**
```json
{ "leave": "LEAVE01348", "action": "Back to Work", "remarks": "Joined at site" }
```
- `leave` (required): `data.leaves[0].name` from 3.1.
- `action` (required): exactly one of `Back to Work`, `Apply New Leave`,
  `Remind Later`.
- `remarks` (optional): free text from the employee.

The comment on the leave reads:
`Leave end reminder response: Back to Work | Remarks: Joined at site`

**Response `data`:**
```json
{ "leave": "LEAVE01348", "action": "Back to Work", "comment": "51aee7c6d4", "show_again": 0 }
```
- `show_again`: `0` after **Back to Work** or **Apply New Leave**, since 3.1 will
  not return this leave again. `1` after **Remind Later**, since 3.1 keeps
  returning it.

**Errors (500):** `action` not one of the three labels; leave does not exist;
leave belongs to another employee; leave is not Approved. Show `message`.

---

## 4. Suggested app flow

1. Call `get_leave_end_reminder` (no params) on app open / home-screen load and on
   resume from background. It is a cheap read, so calling it once per session
   start is enough.
2. If `data.show_reminder == 1`, show a dialog with `data.title`,
   `data.message` and one button per item in `data.actions`. An optional remarks
   field may be shown.
3. When a button is tapped, call `respond_leave_end_reminder` with
   `leave = data.leaves[0].name` and that button's label as `action`. Then:
   - **Back to Work**: close the dialog.
   - **Apply New Leave**: close the dialog and open the app's existing Apply
     Leave screen. This endpoint does not create the leave.
   - **Remind Later**: close the dialog. Show it again on the next app open /
     resume, or after a local timer of your choice (for example 2 hours).
4. If `data.show_reminder == 0`, show nothing.
5. If the dialog is closed without tapping a button, send nothing. It will
   show again on the next call.
6. Do **not** cache the result across days. The answer changes at midnight.

**Local notification (optional):** the app can schedule a local notification for
the morning of `to_date` when it already has the employee's approved leaves.
Still call this endpoint before showing the message, because the leave may have
been amended or cancelled since.

---

## 5. QA checklist

| Case | Expected |
|------|----------|
| Any employee (Site, Office, Field, etc.), Approved full-day leave, `date` = its approved To Date | `show_reminder = 1`, one item in `leaves` |
| Same leave, `date` = its From Date (or any day but the last) | `show_reminder = 0` |
| Short Leave or Half Day ending on `date` | `show_reminder = 0` |
| Leave Pending / Rejected / Cancelled | `show_reminder = 0` |
| Another Approved leave starts the day after `date` | `show_reminder = 0` |
| Leave amended to end earlier | Reminder moves to the new leave's last day |
| Respond **Remind Later**, call 3.1 again | `show_reminder = 1` again; comment added on the leave |
| Respond **Back to Work** or **Apply New Leave**, call 3.1 again | `show_reminder = 0`; comment added on the leave |
| Respond with any other `action` text | 500 with the list of allowed actions |

Example: `GET /api/method/employee_self_service.mobile.v1.leave_reminder.get_leave_end_reminder?date=2026-11-20`
