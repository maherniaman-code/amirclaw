"""Acceptance Pass Runner printing real rendered texts, audit tables, and diagnostics."""

from datetime import datetime, timedelta
import json
from reports_engine.date_utils import DUBAI_TZ
from reports_engine.whatsapp_parser import WhatsAppParser
from reports_engine.deduplicator import deduplicate_events
from reports_engine.sender_resolver import clean_sender_name
from reports_engine.report_generators import (
    generate_parking_report_text,
    generate_maintenance_report_text,
    generate_operations_report_text,
    generate_parking_violations_report_text,
    generate_vehicle_permissions_report_text,
)


def run_all():
    parser = WhatsAppParser()

    print("=" * 60)
    print("ACCEPTANCE PASS - CASES A TO H (REAL DATA & EXACT FIXTURES)")
    print("=" * 60)

    # Load real rows from workspace/whatsapp_messages.json
    with open("workspace/whatsapp_messages.json", "r") as f:
        all_rows = json.load(f)
    rows_by_id = {r["msgId"]: r for r in all_rows if r.get("msgId")}

    # ─────────────────────────────────────────────────────────────
    # CASE A: Smart Parking (Group: Multistory Parking Building Meydan)
    # ─────────────────────────────────────────────────────────────
    print("\n[CASE A: Smart Parking — REAL DATA]")
    case_a_ids = [
        ("12-10 10:00", "ACF2CE144080C5C7B66B1F5E5197DE0C"),
        ("12-10 12:00", "ACEF6DBD6CA24B73B7DEDA2D03D0F53E"),
        ("12-10 14:00", "ACAE4F70741D0AED40EA85A5322F7346"),
        ("13-10 10:00", "ACF4EF7EE179EA8532FA9CDADB09DCC1"),
    ]
    for label, msg_id in case_a_ids:
        r = rows_by_id[msg_id]
        res = parser.parse_smart_parking(r["text"])
        print(f"\n--- {label} (Message ID: {msg_id}, Sender: {clean_sender_name(r['sender'])}) ---")
        print(f"Raw Text Snippet: {repr(r['text'])}")
        print(f"Entered: {res['cars_entered']} | Parked: {res['occupied_slots']} | Empty: {res['empty_slots']} | Outside: {res['outside_cars']}")
        print(f"Diagnostics: {res['diagnostics']}")

    # ─────────────────────────────────────────────────────────────
    # CASE B: Rumble Area (Group: Parking Team at Meydan)
    # ─────────────────────────────────────────────────────────────
    print("\n[CASE B: Rumble Area — REAL DATA]")
    case_b_ids = [
        ("12-10 at 01:30PM", "A5B6E66457F524D85F522182A96CE3B6"),
        ("13-10 at 09:30AM", "A5DD46BA4F6E77909BC8CD214206B423"),
    ]
    for label, msg_id in case_b_ids:
        r = rows_by_id[msg_id]
        res = parser.parse_rumble_area(r["text"])
        print(f"\n--- {label} (Message ID: {msg_id}, Sender: {clean_sender_name(r['sender'])}) ---")
        print(f"Raw Text: {repr(r['text'])}")
        print(f"Total Cars: {res['total_cars']} | Bus: {res['bus']} | Time Written: {res['time_written']}")

    # ─────────────────────────────────────────────────────────────
    # CASE C: Ramp / Service Road (FIXTURE)
    # ─────────────────────────────────────────────────────────────
    print("\n[CASE C: Ramp / Service Road — FIXTURE]")
    fixture_c = (
        "*Parking Occupany Report*\n"
        "\n"
        "Date : 12-10-2026\n"
        "Time 11:30 AM\n"
        "\n"
        "Podium Parking Total Slots :127\n"
        "Customer and visitor cars : 87\n"
        "P.o.d Cars : 05\n"
        "Staff Cars\n"
        ": 118\n"
        "Total cars Entered at ramp area\n"
        " : 210\n"
        "Total cars parked at ramp Area\n"
        " :158\n"
        "Total Slot Service Road\n"
        " : 120\n"
        "Total Car Entered Service Road:\n"
        "131\n"
        "Total car parked at service road\n"
        " : 117\n"
        "Partially Blocked Parking.      :04"
    )
    res_c = parser.parse_ramp_service_road(fixture_c)
    rendered_c = generate_parking_report_text(
        report_date="12-10-2026",
        report_time="11:30",
        email_data=None,
        smart_data=None,
        ramp_data=res_c,
        rumble_data=None,
    )
    print("\n--- Rendered Report Text (Ramp & Service Road Section) ---")
    ramp_section = rendered_c.split("🅿️ *Ramp Area Location*")[1].split("🏁 *Rumble Area*")[0]
    print("🅿️ *Ramp Area Location*\n\n" + ramp_section.strip())
    print(f"\nDiagnostics for Case C: {res_c['diagnostics']}")

    # ─────────────────────────────────────────────────────────────
    # CASE D: Duplicate Detection Precision within single groups (REAL DATA)
    # ─────────────────────────────────────────────────────────────
    print("\n[CASE D: Duplicate Detection Precision — REAL DATA]")
    # 1. Security Team single group
    sec_ids = ["ACCDEB71EFCFFF90E62D71D6C5792617", "AC9EFF07882E9C041A35576539E6497E", "AC5198C4766FAE53661C494A0E0DA4B6"]
    sec_events = []
    for mid in sec_ids:
        r = rows_by_id[mid]
        fl = "3rd Floor" if "3rd floor" in r["text"].lower() else ("2nd Floor" if "2nd floor" in r["text"].lower() else "1st Floor")
        sec_events.append({
            "message_id": r["msgId"],
            "group_name": r["group"],
            "building": "N",
            "floor": fl,
            "text": r["text"],
            "raw_datetime": r["ts"],
            "datetime": datetime(2026, 10, 12, 18, 0, tzinfo=DUBAI_TZ),
        })
    u_sec, unc_sec = deduplicate_events(sec_events)
    print(f"\n1. Meydan - Security Team Only:")
    print(f"Total Unique Events: {len(u_sec)} (Expected: 3 distinct floor events, not merged)")
    for idx, ev in enumerate(u_sec, 1):
        print(f"  Event {idx}: Floor={ev['floor']}, ID={ev['source_message_ids']}, Group={ev['source_groups']}")

    # 2. Fire & Safety single group
    fs_ids = ["ACA5087A6848FF0E87B6092127275200", "ACC5B773B077A069C1E11C59E43DAF9A", "ACDF8A0C27ADCF776BE47776758B5E02"]
    fs_events = []
    for mid in fs_ids:
        r = rows_by_id[mid]
        fl = "3rd Floor" if "3rd floor" in r["text"].lower() else ("2nd Floor" if "2nd floor" in r["text"].lower() else "1st Floor")
        fs_events.append({
            "message_id": r["msgId"],
            "group_name": r["group"],
            "building": "N",
            "floor": fl,
            "text": r["text"],
            "raw_datetime": r["ts"],
            "datetime": datetime(2026, 10, 12, 18, 5, tzinfo=DUBAI_TZ),
        })
    u_fs, unc_fs = deduplicate_events(fs_events)
    print(f"\n2. Fire & Safety Team Meydan Only:")
    print(f"Total Unique Events: {len(u_fs)} (Expected: 3 distinct floor events, not merged)")
    for idx, ev in enumerate(u_fs, 1):
        print(f"  Event {idx}: Floor={ev['floor']}, ID={ev['source_message_ids']}, Group={ev['source_groups']}")

    # ─────────────────────────────────────────────────────────────
    # CASE E: Parking Violations (Report 5) — REAL DATA
    # ─────────────────────────────────────────────────────────────
    print("\n[CASE E: Parking Violations (Report 5) — REAL DATA]")
    ev_row = rows_by_id["ACE3694456C9F52DA59FF052670F061E"]
    ev_e = {
        "message_id": ev_row["msgId"],
        "datetime": datetime(2026, 10, 13, 9, 38, tzinfo=DUBAI_TZ),
        "raw_datetime": ev_row["ts"],
        "sender": clean_sender_name(ev_row["sender"]),
        "group_name": ev_row["group"],
        "text": ev_row["text"],
        "photos": [],
        "quoted_message_id": ev_row.get("quoted", ""),
    }
    parsed_e = parser.parse_parking_violation(ev_e)
    rendered_e = generate_parking_violations_report_text([parsed_e], {"total": 1, "rectified": 0, "pending": 1})
    print("\n--- Rendered Report 5 Text ---")
    print(rendered_e)
    print("\nAudit Table:")
    print("Report Item | Source Message ID                | Source Group                      | Timestamp              | Sender")
    print("-" * 110)
    print(f"Slot 399    | {parsed_e['message_id']} | {parsed_e['group_name'][:33]:33} | {parsed_e['raw_datetime']} | {parsed_e['sender']}")

    # ─────────────────────────────────────────────────────────────
    # CASE F: Vehicle Permission Requests (Report 6) — REAL DATA
    # ─────────────────────────────────────────────────────────────
    print("\n[CASE F: Vehicle Permission Requests (Report 6) — REAL DATA]")
    perm_ids = ["AC5D7527BC37553B2AD9D2824D4CE496", "AC68DF7D12EDF020E9BF5455EB785CD8", "ACB325F153B92C2B6E6E946412F9D91C"]
    parsed_perms = []
    for pid in perm_ids:
        r = rows_by_id[pid]
        p_dict = {
            "message_id": r["msgId"],
            "raw_datetime": r["ts"],
            "sender": clean_sender_name(r["sender"]),
            "group_name": r["group"],
            "text": r["text"],
        }
        parsed_perms.append(parser.parse_vehicle_permission(p_dict))
    rendered_f = generate_vehicle_permissions_report_text(parsed_perms, {"total": 3, "parked": 0, "pending": 3})
    print("\n--- Rendered Report 6 Text ---")
    print(rendered_f)
    print("\nAudit Table:")
    print("Plate No     | Source Message ID                | Source Group           | Timestamp              | Sender")
    print("-" * 105)
    for p in parsed_perms:
        print(f"{p['plate_no']:12} | {p['message_id']} | {p['group_name']:22} | {p['raw_datetime']} | {p['sender']}")

    # ─────────────────────────────────────────────────────────────
    # CASE G: Operations Summary (Report 4) — REAL DATA (Security Team Only)
    # ─────────────────────────────────────────────────────────────
    print("\n[CASE G: Operations Summary (Report 4) — REAL DATA (Security Team Only)]")
    ops_ids = [
        "A53009193A89FCAE167CFF44DBB6B9BC",  # TELECON SIGNS
        "A57D201003A84A124B39D75C7EC5A3EE",  # Housekeeping chairs
        "3A2470B9B163E158CB5D",              # Arki
        "AC30EC6D51AFF1C2A0275B2832F661B6",  # Fit Movers
        "A5ABD1B560299A2DE172A91CCF194496",  # Rose garden
    ]
    ops_items = []
    for oid in ops_ids:
        r = rows_by_id[oid]
        v_name = "TELECON SIGNS" if "TELECON" in r["text"] else ("Housekeeping" if "Housekeeping" in r["text"] else ("Arki" if "Arki" in r["text"] else ("Fit Movers" if "Fit Movers" in r["text"] else "Rose Garden")))
        loc = "Building P" if "P building" in r["text"] else ("Building Q" if "Q building" in r["text"] or "chairs" in r["text"] else ("Building M" if "M building" in r["text"] else ("Building N" if "N-Building" in r["text"] else "GSU Room")))
        ops_items.append({
            "message_id": r["msgId"],
            "sender": clean_sender_name(r["sender"]),
            "group_name": r["group"],
            "raw_datetime": r["ts"],
            "location_heading": loc,
            "vendor": v_name,
            "activity": r["text"].replace("\n", " ")[:90],
            "time_str": r["ts"].split(", ")[-1] if ", " in r["ts"] else r["ts"],
            "status": "Reported",
        })
    rendered_g = generate_operations_report_text(ops_items, {"total": len(ops_items), "completed": 0, "reported": len(ops_items), "vendors": "Arki, Fit Movers, Housekeeping, Rose Garden, TELECON SIGNS"})
    print("\n--- Rendered Report 4 Text ---")
    print(rendered_g)
    print("\nAudit Table:")
    print("Vendor        | Location   | Source Message ID                | Timestamp              | Sender")
    print("-" * 105)
    for o in ops_items:
        print(f"{o['vendor']:13} | {o['location_heading']:10} | {o['message_id']} | {o['raw_datetime']} | {o['sender']}")

    # ─────────────────────────────────────────────────────────────
    # CASE H: Maintenance Summary (Report 2) — REAL DATA (FM Group Only)
    # ─────────────────────────────────────────────────────────────
    print("\n[CASE H: Maintenance Summary (Report 2) — REAL DATA (FM Group Only)]")
    r12 = rows_by_id["A504CD22E4680531E8BF2DDA17BF7DBB"]
    r13 = rows_by_id["A57EC0A29B95DB593D47D30B40023BB7"]
    fm_tickets = [
        {
            "message_id": r12["msgId"],
            "group_name": r12["group"],
            "building": "N",
            "floor": "1st Floor",
            "location_heading": "Building N",
            "issue": r12["text"].replace("\n", " "),
            "text": r12["text"],
            "datetime": datetime(2026, 10, 12, 7, 54, tzinfo=DUBAI_TZ),
            "raw_datetime": r12["ts"],
            "status": "Pending",
        },
        {
            "message_id": r13["msgId"],
            "group_name": r13["group"],
            "building": "N",
            "floor": "1st Floor",
            "location_heading": "Building N",
            "issue": r13["text"].replace("\n", " "),
            "text": r13["text"],
            "datetime": datetime(2026, 10, 13, 7, 52, tzinfo=DUBAI_TZ),
            "raw_datetime": r13["ts"],
            "status": "Pending",
        },
    ]
    u_h, unc_h = deduplicate_events(fm_tickets)
    print(f"Unique Events Count: {len(u_h)} (Must be 2 distinct tickets across days, NOT merged)")
    print(f"Cross-Day Repeat Flag: {[x['type'] for x in unc_h]}")
    rendered_h = generate_maintenance_report_text(u_h, {"completed": 0, "total": 2, "open": 2, "safety_priority": "N/A", "teams": "N/A"})
    print("\n--- Rendered Report 2 Text ---")
    print(rendered_h)
    print("\nAudit Table:")
    print("Ticket Issue Snippet                             | Source Message ID                | Timestamp              | Status")
    print("-" * 115)
    for u in u_h:
        print(f"{u['issue'][:45]:45} | {u['message_id']} | {u['raw_datetime']} | {u['status']}")


if __name__ == "__main__":
    run_all()

