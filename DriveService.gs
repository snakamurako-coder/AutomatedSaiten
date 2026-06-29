/**
 * Google Drive ファイル操作
 */

var IMAGE_MIME_TYPES = {
  'image/jpeg': true,
  'image/png': true,
  'image/jpg': true,
  'application/pdf': true
};

function listFolderFiles(folderId) {
  if (!folderId) throw new Error('フォルダIDを指定してください。');
  var folder = DriveApp.getFolderById(folderId);
  var files = folder.getFiles();
  var list = [];

  while (files.hasNext()) {
    var file = files.next();
    var mime = file.getMimeType();
    if (!IMAGE_MIME_TYPES[mime]) continue;
    list.push({
      id: file.getId(),
      name: file.getName(),
      mimeType: mime,
      isPdf: mime === 'application/pdf'
    });
  }
  list.sort(function(a, b) { return a.name.localeCompare(b.name, 'ja'); });
  return list;
}

function getDriveFileBase64(fileId) {
  var file = DriveApp.getFileById(fileId);
  var mime = file.getMimeType();
  var blob = file.getBlob();
  var bytes = Utilities.base64Encode(blob.getBytes());
  return {
    fileId: fileId,
    fileName: file.getName(),
    mimeType: mime,
    base64: bytes,
    isPdf: mime === 'application/pdf'
  };
}

function saveWarpedImage(base64Image, originalFileName, studentId) {
  var ss = getActiveTestSs();
  var folder = getOrCreateTestImageFolder(ss);
  var imageBytes = base64Image.split(',')[1];
  var safeId = studentId && !String(studentId).includes('?') ? studentId : 'unknown';
  var fileName = '補正_' + safeId + '_' + (originalFileName || 'image') + '.jpg';
  fileName = fileName.replace(/[^\w\u3040-\u30ff\u4e00-\u9faf.\-]/g, '_').substring(0, 200);
  var file = folder.createFile(Utilities.newBlob(Utilities.base64Decode(imageBytes), 'image/jpeg', fileName));
  return { fileId: file.getId(), fileName: fileName };
}

function getProcessedFileIds(ss) {
  ss = ss || getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (sheet.getLastRow() <= 1) return {};
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var fileIdCol = headers.indexOf('ファイルID');
  if (fileIdCol < 0) return {};

  var data = sheet.getRange(2, 1, sheet.getLastRow() - 1, sheet.getLastColumn()).getValues();
  var map = {};
  data.forEach(function(row) {
    if (row[fileIdCol]) map[String(row[fileIdCol])] = true;
  });
  return map;
}

function isFileAlreadyProcessed(ss, fileId) {
  return !!getProcessedFileIds(ss)[fileId];
}

function cropImageRegion(base64Image, region) {
  // サーバー側クロップは Canvas 不可のため、クライアントで行う。
  // 本人欄用に warped 画像全体を返し、クライアントで crop する方式を IdentityService で使用。
  return base64Image;
}

function getWarpedImageForStudent(sourceFileId) {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (sheet.getLastRow() <= 1) return null;
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var fileIdCol = headers.indexOf('ファイルID');
  if (fileIdCol < 0) return null;

  var data = sheet.getDataRange().getValues();
  for (var i = 1; i < data.length; i++) {
    if (String(data[i][fileIdCol]) === String(sourceFileId)) {
      var folder = getOrCreateTestImageFolder(ss);
      var files = folder.getFiles();
      while (files.hasNext()) {
        var f = files.next();
        if (f.getName().indexOf(sourceFileId) >= 0 || f.getName().indexOf(data[i][0]) >= 0) {
          return getDriveFileBase64(f.getId());
        }
      }
    }
  }
  return getDriveFileBase64(sourceFileId);
}

function getStudentWarpedImagesMeta() {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (sheet.getLastRow() <= 1) return [];
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var map = getResultColumnMap(headers);
  var data = sheet.getRange(2, 1, sheet.getLastRow() - 1, sheet.getLastColumn()).getValues();

  return data.map(function(row, idx) {
    return {
      rowIndex: idx + 2,
      studentId: row[map.studentId] || '',
      fileName: row[map.fileName] || '',
      fileId: row[map.fileId] || '',
      name: row[map.name] || ''
    };
  });
}
