import path from 'path';
import { EXCEL_PATH } from '../whatsapp/excel.js';
import { backfillFromExcel, exportMessagesToExcel } from '../cloud/messages.js';
import { runCloudBackup, listCloudBackups, restoreCloudBackup } from '../cloud/backup.js';

const USAGE = `Usage: node dist/scripts/firebase.js <command>
  backfill [excelPath]          Upload all rows of the WhatsApp Excel file to Firestore
  backup                        Run a cloud backup now (session, databases, Excel, new media)
  list                          List cloud backups
  restore [id|latest] [outDir]  Restore a backup into outDir (default: restore/<id>), never the live workspace
  export-excel [outPath]        Rebuild a WhatsApp Excel file from Firestore messages`;

async function main(): Promise<void> {
    const [cmd, a1, a2] = process.argv.slice(2);
    switch (cmd) {
        case 'backfill': {
            const r = await backfillFromExcel(a1 || EXCEL_PATH);
            console.log(`Uploaded ${r.uploaded} messages (skipped ${r.skipped} empty rows, ${r.unparsedDates} unparsed dates).`);
            break;
        }
        case 'backup': {
            const r = await runCloudBackup();
            console.log(JSON.stringify(r, null, 2));
            break;
        }
        case 'list': {
            for (const b of await listCloudBackups()) {
                console.log(`${b.id}  ${(b.sizeBytes / 1024).toFixed(0).padStart(6)} KB  ${b.files.join(', ')}`);
            }
            break;
        }
        case 'restore': {
            const id = a1 || 'latest';
            const outDir = path.resolve(a2 || path.join('restore', id));
            const r = await restoreCloudBackup(id, outDir);
            console.log(`Restored backup ${r.id} into ${outDir}\n  files: ${r.files.join(', ')}\n  media files: ${r.mediaRestored}`);
            console.log('Review the files, stop the bot (pm2 stop amirclaw), then copy what you need into the project folder.');
            break;
        }
        case 'export-excel': {
            const outPath = path.resolve(a1 || path.join('restore', 'whatsapp_messages_from_firebase.xlsx'));
            const n = await exportMessagesToExcel(outPath);
            console.log(`Wrote ${n} messages to ${outPath}`);
            break;
        }
        default:
            console.log(USAGE);
            process.exitCode = cmd ? 1 : 0;
    }
}

main()
    .catch((err) => {
        console.error('❌', err.message);
        process.exitCode = 1;
    })
    .finally(() => setTimeout(() => process.exit(), 100));
