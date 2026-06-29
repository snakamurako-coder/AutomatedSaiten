/**
 * 本人確認欄・ID/氏名照合
 */

function getVerificationData() {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (sheet.getLastRow() <= 1) return { rows: [], identityFields: getIdentityFields(ss) };

  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);
  var data = sheet.getRange(2, 1, sheet.getLastRow() - 1, sheet.getLastColumn()).getValues();

  var rows = data.map(function(row, idx) {
    return {
      rowIndex: idx + 2,
      studentId: String(row[colMap.studentId] || ''),
      fileName: String(row[colMap.fileName] || ''),
      fileId: String(row[colMap.fileId] || ''),
      name: String(row[colMap.name] || ''),
      warpedFileId: ''
    };
  });

  return {
    rows: rows,
    identityFields: getIdentityFields(ss),
    answerFields: getAnswerFields(ss)
  };
}

function updateStudentIdentity(rowIndex, studentId, name) {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);

  if (colMap.studentId >= 0) sheet.getRange(rowIndex, colMap.studentId + 1).setValue(studentId || '');
  if (colMap.name >= 0) sheet.getRange(rowIndex, colMap.name + 1).setValue(name || '');
  return true;
}

function verifyIdentityWithRoster(rosterRows) {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (sheet.getLastRow() <= 1) return [];

  var rosterMap = {};
  (rosterRows || []).forEach(function(r) {
    if (r.studentId) rosterMap[String(r.studentId).trim()] = String(r.name || '').trim();
  });

  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);
  var data = sheet.getRange(2, 1, sheet.getLastRow() - 1, sheet.getLastColumn()).getValues();
  var results = [];

  for (var i = 0; i < data.length; i++) {
    var row = data[i];
    var sid = String(row[colMap.studentId] || '').trim();
    var name = String(row[colMap.name] || '').trim();
    var rosterName = sid ? (rosterMap[sid] || '') : '';
    var idMatch = !!sid;
    var nameMatch = !rosterName || !name || rosterName === name;
    var status = 'ok';
    if (!sid) status = 'no_id';
    else if (!nameMatch) status = 'name_mismatch';
    else if (rosterName && !name) status = 'name_empty';

    results.push({
      rowIndex: i + 2,
      studentId: sid,
      name: name,
      rosterName: rosterName,
      fileName: String(row[colMap.fileName] || ''),
      fileId: String(row[colMap.fileId] || ''),
      status: status,
      idMatch: idMatch,
      nameMatch: nameMatch
    });
  }
  return results;
}

function parseRosterCsv(csvText) {
  var lines = String(csvText || '').split(/\r?\n/).filter(function(l) { return l.trim(); });
  var rows = [];
  lines.forEach(function(line, idx) {
    var parts = line.split(/[,;\t]/);
    if (parts.length < 2) return;
    if (idx === 0 && (parts[0].indexOf('ID') >= 0 || parts[0].indexOf('生徒') >= 0)) return;
    rows.push({ studentId: parts[0].trim(), name: parts[1].trim() });
  });
  return rows;
}

function saveWarpedFileMapping(sourceFileId, warpedFileId) {
  PropertiesService.getScriptProperties().setProperty('WARP_' + sourceFileId, warpedFileId);
  return true;
}

function getWarpedFileIdForSource(sourceFileId) {
  return PropertiesService.getScriptProperties().getProperty('WARP_' + sourceFileId) || '';
}

function registerWarpedMapping(sourceFileId, warpedFileId) {
  if (sourceFileId && warpedFileId) {
    PropertiesService.getScriptProperties().setProperty('WARP_' + sourceFileId, warpedFileId);
  }
  return true;
}

function getWarpedImageBase64(sourceFileId) {
  var warpedId = getWarpedFileIdForSource(sourceFileId);
  if (warpedId) {
    try {
      return getDriveFileBase64(warpedId);
    } catch (e) { /* fallback */ }
  }
  var ss = getActiveTestSs();
  var folder = getOrCreateTestImageFolder(ss);
  var files = folder.getFilesByName('補正_' + sourceFileId);
  if (files.hasNext()) return getDriveFileBase64(files.next().getId());

  return getDriveFileBase64(sourceFileId);
}
