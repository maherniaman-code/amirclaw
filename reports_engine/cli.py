"""CLI Entrypoint for Meydan Reports Engine."""

import argparse
import json
import sys
from pathlib import Path

from .manager import ReportsManager
from .corrections_db import save_correction, list_corrections
from .data_quality import generate_data_quality


def main():
    parser = argparse.ArgumentParser(description="Meydan Reports Generator CLI")
    parser.add_argument("--report", choices=["parking", "maintenance", "fire_safety", "operations", "violations", "permissions", "cleaning", "data_quality", "list_corrections"], required=True)
    parser.add_argument("--slot", default="latest", help="Parking slot time (e.g. 10:00, 12:00, 14:00, latest)")
    parser.add_argument("--date", default=None, help="Target report date (DD-MM-YYYY)")
    parser.add_argument("--shift", choices=["Day", "Night"], default=None, help="Shift for maintenance/fire_safety/operations (default: shift in progress)")
    parser.add_argument("--excel", default=None, help="Path to whatsapp_messages.xlsx file")

    # Correction options
    parser.add_argument("--msg-id", default=None, help="Message ID for manual correction")
    parser.add_argument("--target-report", default=None, help="Target report for correction")
    parser.add_argument("--target-status", default=None, help="Target status for correction")

    args = parser.parse_args()

    excel_path = Path(args.excel) if args.excel else None
    manager = ReportsManager(excel_path)

    try:
        # Manual correction mode
        if args.msg_id and args.target_report:
            row_id = save_correction(
                message_id=args.msg_id,
                target_report=args.target_report,
                target_status=args.target_status,
                corrected_by="cli",
            )
            print(json.dumps({"correction_saved": True, "row_id": row_id, "message_id": args.msg_id, "target_report": args.target_report, "target_status": args.target_status}, ensure_ascii=False))
            return

        if args.report == "parking":
            res = manager.generate_parking(slot_request=args.slot, report_date=args.date)
            print(json.dumps(res, ensure_ascii=False))
        elif args.report == "maintenance":
            res = manager.generate_maintenance(target_date=args.date, target_shift=args.shift)
            print(json.dumps(res, ensure_ascii=False))
        elif args.report == "fire_safety":
            res = manager.generate_fire_safety(target_date=args.date, target_shift=args.shift)
            print(json.dumps(res, ensure_ascii=False))
        elif args.report == "operations":
            res = manager.generate_operations(target_date=args.date, target_shift=args.shift)
            print(json.dumps(res, ensure_ascii=False))
        elif args.report == "violations":
            res = manager.generate_parking_violations(target_date=args.date)
            print(json.dumps(res, ensure_ascii=False))
        elif args.report == "permissions":
            res = manager.generate_vehicle_permissions(target_date=args.date)
            print(json.dumps(res, ensure_ascii=False))
        elif args.report == "cleaning":
            res = manager.generate_cleaning(target_date=args.date)
            print(json.dumps(res, ensure_ascii=False))
        elif args.report == "data_quality":
            res = generate_data_quality(manager)
            print(json.dumps(res, ensure_ascii=False))
        elif args.report == "list_corrections":
            corrs = list_corrections()
            print(json.dumps({"corrections": corrs}, ensure_ascii=False))
    except Exception as e:
        err_res = {
            "error": True,
            "message": str(e),
            "type": type(e).__name__,
        }
        print(json.dumps(err_res, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
