import frappe
from frappe import _
from ..controllers.get_employee_attendance import (
    get_employee_attendance,
    get_employee_overtime_attendance,
)
from datetime import datetime
import frappe.utils

SETTINGS_DOCTYPE = "Quantumberg Custom Payroll Settings"

@frappe.whitelist()
def add_attendance_data(payroll_entry):
    maximum_monthly_hours = frappe.db.get_single_value(
        SETTINGS_DOCTYPE, "maximum_monthly_hours"
    )
    overtime_15 = frappe.db.get_single_value(SETTINGS_DOCTYPE, "overtime_15_activity")
    overtime_20 = frappe.db.get_single_value(SETTINGS_DOCTYPE, "overtime_20_activity")

    salary_slips = frappe.db.get_all(
        "Salary Slip", filters={"payroll_entry": payroll_entry, "docstatus": 0}
    )

    for entry in salary_slips:
        salary_slip = frappe.get_doc("Salary Slip", entry.get("name"))
        salary_slip.attendance = []
        salary_slip.regular_overtime = []
        salary_slip.holiday_overtime = []

        salary_slip.regular_working_hours = 0
        salary_slip.overtime_hours = 0
        salary_slip.holiday_hours = 0

        attendance = get_employee_attendance(
            salary_slip.employee, salary_slip.start_date, salary_slip.end_date
        )
        overtime_attendance = get_employee_overtime_attendance(
            salary_slip.employee, salary_slip.start_date, salary_slip.end_date
        )
        holiday_dates = get_holiday_dates(salary_slip.employee)

        if attendance:
            for attendance_entry in attendance:
                if (
                    attendance_entry.get("attendance_date") not in (holiday_dates or [])
                    and attendance_entry.get("working_hours") > 0
                ):
                    billiable_hours = 0

                    if not attendance_entry.get("include_unpaid_breaks"):
                        billiable_hours = attendance_entry.get("payment_hours")
                    else:
                        if attendance_entry.get("working_hours") > attendance_entry.get(
                            "min_hours_to_include_a_break"
                        ):
                            billiable_hours = attendance_entry.get("working_hours") - (
                                attendance_entry.get("unpaid_breaks_minutes") / 60
                            )
                        else:
                            billiable_hours = attendance_entry.get("working_hours")

                    salary_slip.append(
                        "attendance",
                        {
                            "attendance_date": attendance_entry.get("attendance_date"),
                            "hours_worked": attendance_entry.get("working_hours"),
                            "include_unpaid_breaks": attendance_entry.get(
                                "include_unpaid_breaks"
                            ),
                            "unpaid_breaks_minutes": attendance_entry.get(
                                "unpaid_breaks_minutes"
                            ),
                            "min_hours_to_include_a_break": attendance_entry.get(
                                "min_hours_to_include_a_break"
                            ),
                            "billiable_hours": billiable_hours,
                        },
                    )

                    salary_slip.regular_working_hours += billiable_hours

        if overtime_attendance:
            for overtime_attendance_record in overtime_attendance:
                if overtime_attendance_record.get("activity_type") == overtime_15:
                    salary_slip.append(
                        "regular_overtime",
                        {
                            "timesheet": overtime_attendance_record.get("name"),
                            "hours": overtime_attendance_record.get("total_hours"),
                        },
                    )
                    salary_slip.overtime_hours += overtime_attendance_record.get(
                        "total_hours"
                    )

                if overtime_attendance_record.get("activity_type") == overtime_20:
                    salary_slip.append(
                        "holiday_overtime",
                        {
                            "timesheet": overtime_attendance_record.get("name"),
                            "hours": overtime_attendance_record.get("total_hours"),
                        },
                    )
                    salary_slip.holiday_hours += overtime_attendance_record.get(
                        "total_hours"
                    )

        # Deduct leave hours from regular working hours
        leave_hours = get_employee_leave_hours(
            salary_slip.employee, salary_slip.start_date, salary_slip.end_date
        )
        salary_slip.regular_working_hours -= leave_hours

        if salary_slip.regular_working_hours > maximum_monthly_hours:
            salary_slip.overtime_hours += (
                salary_slip.regular_working_hours - maximum_monthly_hours
            )
            salary_slip.regular_working_hours = maximum_monthly_hours
        elif salary_slip.regular_working_hours < maximum_monthly_hours:
            balance_to_maximum = maximum_monthly_hours - salary_slip.regular_working_hours
            if salary_slip.overtime_hours <= balance_to_maximum:
                salary_slip.regular_working_hours += salary_slip.overtime_hours
                salary_slip.overtime_hours = 0
            else:
                salary_slip.overtime_hours -= balance_to_maximum
                salary_slip.regular_working_hours += balance_to_maximum

        if (
            salary_slip.attendance
            or salary_slip.regular_overtime
            or salary_slip.holiday_overtime
        ):
            salary_slip.save(ignore_permissions=True)
            frappe.db.commit()


def get_employee_leave_hours(employee, start_date, end_date):
    # Get assigned shift type
    shift_type_name = frappe.db.get_value(
        "Shift Assignment",
        filters={
            "employee": employee,
            "start_date": ["<=", end_date],
            "end_date": [">=", start_date],
            "docstatus": 1,
        },
        fieldname="shift_type",
    )

    if not shift_type_name:
        frappe.throw(_("No Shift Assignment found for employee during the period."))

    shift = frappe.get_doc("Shift Type", shift_type_name)

    # Calculate shift hours
    shift_start = datetime.strptime(str(shift.start_time), "%H:%M:%S")
    shift_end = datetime.strptime(str(shift.end_time), "%H:%M:%S")
    shift_hours = (shift_end - shift_start).seconds / 3600.0

    leave_applications = frappe.db.get_all(
        "Leave Application",
        filters={
            "employee": employee,
            "from_date": ["<=", end_date],
            "to_date": [">=", start_date],
            "docstatus": 1,
            "status": "Approved",
        },
        fields=["from_date", "to_date", "half_day"],
    )

    leave_hours = 0
    for leave in leave_applications:
        leave_days = frappe.utils.date_diff(
            min(leave["to_date"], end_date), max(leave["from_date"], start_date)
        ) + 1

        if leave.get("half_day"):
            leave_hours += shift_hours / 2
        else:
            leave_hours += leave_days * shift_hours

    return leave_hours


def get_holiday_dates(employee):
    holiday_list = frappe.db.get_value("Employee", employee, "holiday_list")
    if holiday_list:
        return frappe.db.get_all(
            "Holiday", filters={"parent": holiday_list}, pluck="holiday_date"
        )
    return None
