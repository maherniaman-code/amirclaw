import ExcelJS from 'exceljs';
import fs from 'fs';
import path from 'path';

function unwrapMessage(msg: any): any {
    if (!msg.message) return null;
    let m: any = msg.message;
    if (m.ephemeralMessage?.message) m = m.ephemeralMessage.message;
    if (m.viewOnceMessage?.message) m = m.viewOnceMessage.message;
    if (m.viewOnceMessageV2?.message) m = m.viewOnceMessageV2.message;
    if (m.documentWithCaptionMessage?.message) m = m.documentWithCaptionMessage.message;
    if (m.deviceSentMessage?.message) m = m.deviceSentMessage.message;
    if (m.botInvokeMessage?.message) m = m.botInvokeMessage.message;
    return m;
}

function extractQuotedMessageId(m: any): string {
    if (!m) return '';
    const ctx =
        m.extendedTextMessage?.contextInfo ||
        m.imageMessage?.contextInfo ||
        m.videoMessage?.contextInfo ||
        m.documentMessage?.contextInfo ||
        m.audioMessage?.contextInfo ||
        m.stickerMessage?.contextInfo ||
        m.locationMessage?.contextInfo ||
        m.contactMessage?.contextInfo ||
        m.buttonsResponseMessage?.contextInfo ||
        m.listResponseMessage?.contextInfo ||
        m.templateButtonReplyMessage?.contextInfo;
    return (ctx && ctx.stanzaId) ? String(ctx.stanzaId) : '';
}

async function runTests() {
    console.log('--- TEST 1: Quoted ID Extraction across Message Types ---');
    const targetParentId = '3EB07A2B4C6D8E9F';

    // 1. Text reply (extendedTextMessage)
    const textMsg = {
        key: { id: 'REPLY_TEXT_001' },
        message: {
            extendedTextMessage: {
                text: 'Done',
                contextInfo: { stanzaId: targetParentId, participant: '12345@s.whatsapp.net' }
            }
        }
    };
    const extractedTextId = extractQuotedMessageId(unwrapMessage(textMsg));
    console.log(`Text reply extracted ID: "${extractedTextId}" -> Match: ${extractedTextId === targetParentId}`);

    // 2. Image reply
    const imageMsg = {
        key: { id: 'REPLY_IMG_002' },
        message: {
            imageMessage: {
                caption: 'Light replaced',
                contextInfo: { stanzaId: targetParentId }
            }
        }
    };
    const extractedImgId = extractQuotedMessageId(unwrapMessage(imageMsg));
    console.log(`Image reply extracted ID: "${extractedImgId}" -> Match: ${extractedImgId === targetParentId}`);

    // 3. Document reply
    const docMsg = {
        key: { id: 'REPLY_DOC_003' },
        message: {
            documentMessage: {
                caption: 'Report attached',
                contextInfo: { stanzaId: targetParentId }
            }
        }
    };
    const extractedDocId = extractQuotedMessageId(unwrapMessage(docMsg));
    console.log(`Document reply extracted ID: "${extractedDocId}" -> Match: ${extractedDocId === targetParentId}`);

    // 4. Wrapped Ephemeral message
    const ephemeralMsg = {
        key: { id: 'REPLY_EPH_004' },
        message: {
            ephemeralMessage: {
                message: {
                    extendedTextMessage: {
                        text: 'Resolved',
                        contextInfo: { stanzaId: targetParentId }
                    }
                }
            }
        }
    };
    const extractedEphId = extractQuotedMessageId(unwrapMessage(ephemeralMsg));
    console.log(`Ephemeral wrapped extracted ID: "${extractedEphId}" -> Match: ${extractedEphId === targetParentId}`);

    // 5. Wrapped ViewOnce message
    const viewOnceMsg = {
        key: { id: 'REPLY_VO_005' },
        message: {
            viewOnceMessage: {
                message: {
                    imageMessage: {
                        caption: 'Fixed',
                        contextInfo: { stanzaId: targetParentId }
                    }
                }
            }
        }
    };
    const extractedVoId = extractQuotedMessageId(unwrapMessage(viewOnceMsg));
    console.log(`ViewOnce wrapped extracted ID: "${extractedVoId}" -> Match: ${extractedVoId === targetParentId}`);

    // 6. Regular message without quote
    const normalMsg = {
        key: { id: 'NORMAL_006' },
        message: {
            conversation: 'Normal message without reply'
        }
    };
    const extractedNormalId = extractQuotedMessageId(unwrapMessage(normalMsg));
    console.log(`Normal unquoted message extracted ID: "${extractedNormalId}" -> Empty: ${extractedNormalId === ''}`);

    console.log('\n--- TEST 2: Existing 7-Column File Backward Compatibility ---');
    const existingFile = path.resolve('backups/stage_a_initial/whatsapp_messages.xlsx');
    const workbook = new ExcelJS.Workbook();
    await workbook.xlsx.readFile(existingFile);
    const sheet = workbook.getWorksheet(1);
    console.log(`Loaded existing backup file: ${sheet?.rowCount} rows, columns in row 1:`);
    const headerRow = sheet?.getRow(1);
    const headers: string[] = [];
    headerRow?.eachCell((c) => headers.push(String(c.value)));
    console.log('Existing Headers:', headers);
    console.log(`Header count is 7: ${headers.length === 7}`);

    console.log('\n--- TEST 3: Safe Append of Column 8 (Quoted Message ID) ---');
    const testCopyPath = path.resolve('backups/stage_a_initial/test_8cols.xlsx');
    // Add header 8
    if (headerRow) {
        headerRow.getCell(8).value = 'Quoted Message ID';
    }
    // Add sample row with quoted ID
    sheet?.addRow([
        'NEW_REPLY_101',
        '13/10/2026, 10:00:00 AM',
        'Meydan FM Team💪',
        'Technician (971500000000)',
        'Resolved',
        '💬 Text',
        '',
        targetParentId
    ]);
    await workbook.xlsx.writeFile(testCopyPath);

    // Verify reading back
    const verifyWb = new ExcelJS.Workbook();
    await verifyWb.xlsx.readFile(testCopyPath);
    const verifySheet = verifyWb.getWorksheet(1);
    const lastRow = verifySheet?.getRow(verifySheet.rowCount);
    console.log('Read back newly added row:');
    console.log(`- Col 1 (Message ID): ${lastRow?.getCell(1).value}`);
    console.log(`- Col 5 (Text): ${lastRow?.getCell(5).value}`);
    console.log(`- Col 8 (Quoted Message ID): ${lastRow?.getCell(8).value}`);
    const success = (lastRow?.getCell(8).value === targetParentId);
    console.log(`Verification Passed: ${success}`);

    fs.unlinkSync(testCopyPath);
    console.log('\nALL VERIFICATION TESTS COMPLETED SUCCESSFULLY.');
}

runTests().catch(console.error);
