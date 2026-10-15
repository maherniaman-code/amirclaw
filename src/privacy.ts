// HR / leave messages (names, staff IDs, leave dates) stay on the server only: they are not
// mirrored to Firestore and are removed from the cloud-backup copy of the message database.
// Keep in sync with exclude.leave_markers in config/meydan_rules.yaml (a Python test checks).
export const HR_MARKERS = [
    'leave details',
    'start date',
    'staff id',
    'staff is',
    'emp id',
    'total days',
    'annual leave',
    'taking leave',
    'on leave',
    'leave request',
];

export function isHrMessage(text: string | undefined | null): boolean {
    const t = (text || '').toLowerCase();
    return HR_MARKERS.some((m) => t.includes(m));
}
