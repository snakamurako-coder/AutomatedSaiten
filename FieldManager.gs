/**
 * 記述欄・本人確認欄・配点の管理
 */

function getAnswerFields(ss) {
  ss = ss || getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_ANSWER_FIELDS);
  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return [];

  var fields = [];
  for (var i = 1; i < data.length; i++) {
    if (!data[i][0]) continue;
    fields.push({
      id: String(data[i][0]),
      displayName: String(data[i][1] || data[i][0]),
      x: parseInt(data[i][2], 10) || 0,
      y: parseInt(data[i][3], 10) || 0,
      width: parseInt(data[i][4], 10) || 0,
      height: parseInt(data[i][5], 10) || 0,
      order: parseInt(data[i][6], 10) || i
    });
  }
  fields.sort(function(a, b) { return a.order - b.order; });
  return fields;
}

function saveAnswerFields(fields) {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_ANSWER_FIELDS);
  sheet.clear();
  sheet.appendRow(['記述欄ID', '表示名', 'x', 'y', 'width', 'height', '表示順']);

  fields.forEach(function(f, idx) {
    sheet.appendRow([
      f.id,
      f.displayName || f.id,
      f.x, f.y, f.width, f.height,
      f.order != null ? f.order : idx + 1
    ]);
  });

  syncPointsSheet(ss, fields);
  rebuildResultsSheetHeaders(ss);
  return getAnswerFields(ss);
}

function syncPointsSheet(ss, fields) {
  var sheet = ss.getSheetByName(SHEET_POINTS);
  var existing = {};
  if (sheet.getLastRow() > 1) {
    var data = sheet.getDataRange().getValues();
    for (var i = 1; i < data.length; i++) {
      existing[String(data[i][0])] = data[i][1];
    }
  }
  sheet.clear();
  sheet.appendRow(['記述欄ID', '満点']);
  fields.forEach(function(f) {
    sheet.appendRow([f.id, existing[f.id] != null ? existing[f.id] : 5]);
  });
}

function savePoints(pointsMap) {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_POINTS);
  var data = sheet.getDataRange().getValues();
  for (var i = 1; i < data.length; i++) {
    var id = String(data[i][0]);
    if (pointsMap[id] != null) {
      sheet.getRange(i + 1, 2).setValue(parseInt(pointsMap[id], 10) || 0);
    }
  }
  return getPointsMap(ss);
}

function getPointsMap(ss) {
  ss = ss || getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_POINTS);
  var data = sheet.getDataRange().getValues();
  var map = {};
  for (var i = 1; i < data.length; i++) {
    map[String(data[i][0])] = parseInt(data[i][1], 10) || 0;
  }
  return map;
}

function getIdentityFields(ss) {
  ss = ss || getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_IDENTITY_FIELDS);
  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return [];

  var fields = [];
  for (var i = 1; i < data.length; i++) {
    if (!data[i][0]) continue;
    fields.push({
      type: String(data[i][0]),
      x: parseInt(data[i][1], 10) || 0,
      y: parseInt(data[i][2], 10) || 0,
      width: parseInt(data[i][3], 10) || 0,
      height: parseInt(data[i][4], 10) || 0
    });
  }
  return fields;
}

function saveIdentityFields(fields) {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_IDENTITY_FIELDS);
  sheet.clear();
  sheet.appendRow(['欄種別', 'x', 'y', 'width', 'height']);
  fields.forEach(function(f) {
    sheet.appendRow([f.type, f.x, f.y, f.width, f.height]);
  });
  return getIdentityFields(ss);
}

function fieldsToBoxes(fields) {
  return fields.map(function(f) {
    return { id: f.id, x: f.x, y: f.y, w: f.width, h: f.height };
  });
}
