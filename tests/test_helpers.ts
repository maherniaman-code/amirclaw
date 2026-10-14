export function generateParkingReportText(data: any): string {
    const val = (obj: any, key: string, defaultVal = 'N/A') => {
        if (!obj || obj[key] === undefined || obj[key] === null) return defaultVal;
        return String(obj[key]);
    };

    const p = data.p_bld || {};
    const n = data.n_bld || {};
    const m = data.m_bld || {};
    const q = data.q_bld || {};
    const sum = data.summary || {};
    const smart = data.smart || {};
    const ramp = data.ramp || {};
    const rumble = data.rumble || {};

    return (
        `🅿️ *MEYDAN PARKING*\n` +
        `📆 Date: ${data.date}\n` +
        `⏰ Time: ${data.time}\n` +
        `\n` +
        `🏢 *P BUILDING*\n` +
        `\n` +
        `• Total Slots: 71\n` +
        `• Cars Parked: ${val(p, 'cars_parked')} 🚗\n` +
        `• Empty Slots: ${val(p, 'empty_slots')} ✅\n` +
        `\n` +
        `🏢 *N BUILDING*\n` +
        `\n` +
        `• Total Slots: 77\n` +
        `• Cars Parked: ${val(n, 'cars_parked')} 🚗\n` +
        `• Empty Slots: ${val(n, 'empty_slots')} ✅\n` +
        `\n` +
        `🏢 *M BUILDING*\n` +
        `\n` +
        `• Total Slots: 165\n` +
        `• Cars Parked: ${val(m, 'cars_parked')} 🚗\n` +
        `• Empty Slots: ${val(m, 'empty_slots')} ✅\n` +
        `\n` +
        `🏢 *Q BUILDING*\n` +
        `\n` +
        `• Total Slots: 146\n` +
        `• Cars Parked: ${val(q, 'cars_parked')} 🚗\n` +
        `• Empty Slots: ${val(q, 'empty_slots')} ✅\n` +
        `\n` +
        `📊 *TOTAL SUMMARY* = 459\n` +
        `\n` +
        `• Total Cars Entered: ${val(sum, 'total_cars_entered')} ⬇️\n` +
        `• Total Cars Parked: ${val(sum, 'total_cars_parked')}\n` +
        `• Total Empty Slots: ${val(sum, 'total_empty_slots')}\n` +
        `\n` +
        `🅿️ *SMART PARKING SUMMARY REPORT*\n` +
        `\n` +
        `• Total Slots : 887\n` +
        `• Cars entered: ${val(smart, 'cars_entered')}\n` +
        `• Occupied slots : ${val(smart, 'occupied_slots')}\n` +
        `• Total Empty Slots : ${val(smart, 'empty_slots')}\n` +
        `• Outside smart parking cars: ${val(smart, 'outside_cars')}\n` +
        `\n` +
        `🅿️ *Ramp Area Location*\n` +
        `\n` +
        `• Total Slots Designated : 127\n` +
        `• Customer and Visitors: ${val(ramp, 'customer_visitor')}\n` +
        `• P.O.D: ${val(ramp, 'pod_cars')}\n` +
        `• Staff: ${val(ramp, 'staff_cars')}\n` +
        `• Total car Entered : ${val(ramp, 'total_cars_entered_ramp')}\n` +
        `• Total Car Parking at Ramp Area: ${val(ramp, 'total_cars_parked_ramp')}\n` +
        `\n` +
        `🛣️ *Service Road*\n` +
        `\n` +
        `• Total Slots : 127\n` +
        `• Cars entered: ${val(ramp, 'service_road_entered')}\n` +
        `• Total Occupied Slots : ${val(ramp, 'service_road_parked')}\n` +
        `• Partially Blocked Parking : ${val(ramp, 'partially_blocked_parking')}\n` +
        `\n` +
        `🏁 *Rumble Area*\n` +
        `\n` +
        `• Total Cars: ${val(rumble, 'total_cars')} 🚗\n` +
        `• Bus 🚌: ${val(rumble, 'bus')}`
    );
}
