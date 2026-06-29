/**
 * シート名定数・テスト用スプレッドシート構築
 */

var SHEET_HUB_TEST_LIST = 'テスト一覧';

var SHEET_TEST_INFO = 'テスト情報';
var SHEET_ANSWER_FIELDS = '記述欄情報';
var SHEET_POINTS = '配点情報';
var SHEET_RESULTS = '採点結果';
var SHEET_CRITERIA = '採点基準';
var SHEET_SUMMARY = '考査総括';
var SHEET_DOMAINS = '領域設定';
var SHEET_IDENTITY_FIELDS = '本人確認欄情報';
var SHEET_EXTERNAL_SCORES = '外部連携得点';

var TEST_INFO_KEYS = [
  'テスト名', '科目名', '実施日時', '作成日時',
  '模範解答画像FileID', '生徒解答フォルダID',
  '基準画像幅', '基準画像高さ', 'ステータス'
];

function buildTestSheets(ss) {
  Object.values(getAllTestSheetNames()).forEach(function(name) {
    if (!ss.getSheetByName(name)) {
      ss.insertSheet(name);
    }
  });

  initTestInfoSheet(ss.getSheetByName(SHEET_TEST_INFO));
  initAnswerFieldsSheet(ss.getSheetByName(SHEET_ANSWER_FIELDS));
  initPointsSheet(ss.getSheetByName(SHEET_POINTS));
  initResultsSheet(ss.getSheetByName(SHEET_RESULTS), []);
  initCriteriaSheet(ss.getSheetByName(SHEET_CRITERIA));
  initSummarySheet(ss.getSheetByName(SHEET_SUMMARY));
  initDomainsSheet(ss.getSheetByName(SHEET_DOMAINS));
  initIdentityFieldsSheet(ss.getSheetByName(SHEET_IDENTITY_FIELDS));
  initExternalScoresSheet(ss.getSheetByName(SHEET_EXTERNAL_SCORES));

  const defaultSheet = ss.getSheetByName('シート1');
  if (defaultSheet) ss.deleteSheet(defaultSheet);
  ss.setActiveSheet(ss.getSheetByName(SHEET_TEST_INFO));
}

function getAllTestSheetNames() {
  return {
    TEST_INFO: SHEET_TEST_INFO,
    ANSWER_FIELDS: SHEET_ANSWER_FIELDS,
    POINTS: SHEET_POINTS,
    RESULTS: SHEET_RESULTS,
    CRITERIA: SHEET_CRITERIA,
    SUMMARY: SHEET_SUMMARY,
    DOMAINS: SHEET_DOMAINS,
    IDENTITY_FIELDS: SHEET_IDENTITY_FIELDS,
    EXTERNAL_SCORES: SHEET_EXTERNAL_SCORES
  };
}

function initTestInfoSheet(sheet) {
  if (sheet.getLastRow() > 0) return;
  TEST_INFO_KEYS.forEach(function(key) {
    sheet.appendRow([key, '']);
  });
  sheet.setColumnWidth(1, 200);
  sheet.setColumnWidth(2, 350);
}

function initAnswerFieldsSheet(sheet) {
  if (sheet.getLastRow() > 0) return;
  sheet.appendRow(['記述欄ID', '表示名', 'x', 'y', 'width', 'height', '表示順']);
  sheet.setFrozenRows(1);
}

function initPointsSheet(sheet) {
  if (sheet.getLastRow() > 0) return;
  sheet.appendRow(['記述欄ID', '満点']);
  sheet.setFrozenRows(1);
}

function initCriteriaSheet(sheet) {
  if (sheet.getLastRow() > 0) return;
  sheet.appendRow(['記述欄ID', '解答パターン', '判定', '付与得点', '備考']);
  sheet.setFrozenRows(1);
}

function initSummarySheet(sheet) {
  if (sheet.getLastRow() > 0) return;
  sheet.appendRow(['区分', '項目', '値', '備考']);
  sheet.setFrozenRows(1);
}

function initDomainsSheet(sheet) {
  if (sheet.getLastRow() > 0) return;
  sheet.appendRow(['記述欄ID', '大問', '範囲', '能力']);
  sheet.setFrozenRows(1);
}

function initIdentityFieldsSheet(sheet) {
  if (sheet.getLastRow() > 0) return;
  sheet.appendRow(['欄種別', 'x', 'y', 'width', 'height']);
  sheet.setFrozenRows(1);
}

function initExternalScoresSheet(sheet) {
  if (sheet.getLastRow() > 0) return;
  sheet.appendRow(['生徒ID', '外部得点', 'ソース', 'インポート日時']);
  sheet.setFrozenRows(1);
}

function buildResultHeaders(fields, extraColumns) {
  var headers = ['生徒ID', 'ファイル名', 'ファイルID', '氏名'];
  fields.forEach(function(f) {
    var label = f.displayName || f.id;
    headers.push(label + '_テキスト');
    headers.push(label + '_判定');
    headers.push(label + '_得点');
  });
  (extraColumns || []).forEach(function(col) {
    headers.push(col);
  });
  return headers;
}

function initResultsSheet(sheet, fields, extraColumns) {
  var headers = buildResultHeaders(fields, extraColumns);
  sheet.clear();
  sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  sheet.setFrozenRows(1);
}

function rebuildResultsSheetHeaders(ss) {
  var fields = getAnswerFields(ss);
  var extra = getDynamicResultExtraColumns(ss);
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  var oldData = [];
  if (sheet.getLastRow() > 1) {
    var numRows = sheet.getLastRow() - 1;
    var numCols = sheet.getLastColumn();
    oldData = sheet.getRange(2, 1, numRows, numCols).getValues();
  }
  var oldHeaders = sheet.getLastRow() >= 1 ? sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0] : [];
  initResultsSheet(sheet, fields, extra);
  if (oldData.length === 0) return;

  var newHeaders = buildResultHeaders(fields, extra);
  oldData.forEach(function(row) {
    var newRow = mapResultRow(oldHeaders, row, newHeaders);
    sheet.appendRow(newRow);
  });
}

function getDynamicResultExtraColumns(ss) {
  var cols = [];
  var domainLabels = getDomainColumnLabels(ss);
  domainLabels.forEach(function(l) { cols.push(l); });
  cols.push('外部連携得点');
  cols.push('総計点');
  return cols;
}

function getDomainColumnLabels(ss) {
  var sheet = ss.getSheetByName(SHEET_DOMAINS);
  if (!sheet || sheet.getLastRow() <= 1) return [];
  var data = sheet.getDataRange().getValues();
  var daiMon = {}, hanI = {}, noryoku = {};
  for (var i = 1; i < data.length; i++) {
    if (data[i][1]) daiMon[String(data[i][1])] = true;
    if (data[i][2]) hanI[String(data[i][2])] = true;
    if (data[i][3]) noryoku[String(data[i][3])] = true;
  }
  var labels = [];
  Object.keys(daiMon).sort().forEach(function(k) { labels.push('大問' + k + '_得点'); });
  Object.keys(hanI).sort().forEach(function(k) { labels.push('範囲' + k + '_得点'); });
  Object.keys(noryoku).sort().forEach(function(k) { labels.push('能力' + k + '_得点'); });
  return labels;
}

function getResultColumnMap(headers) {
  var map = {
    studentId: headers.indexOf('生徒ID'),
    fileName: headers.indexOf('ファイル名'),
    fileId: headers.indexOf('ファイルID'),
    name: headers.indexOf('氏名'),
    fields: {},
    extras: {}
  };
  for (var i = 0; i < headers.length; i++) {
    var h = String(headers[i]);
    var textMatch = h.match(/^(.+)_テキスト$/);
    var judgeMatch = h.match(/^(.+)_判定$/);
    var scoreMatch = h.match(/^(.+)_得点$/);
    if (textMatch) {
      if (!map.fields[textMatch[1]]) map.fields[textMatch[1]] = {};
      map.fields[textMatch[1]].text = i;
    } else if (judgeMatch) {
      if (!map.fields[judgeMatch[1]]) map.fields[judgeMatch[1]] = {};
      map.fields[judgeMatch[1]].judgment = i;
    } else if (scoreMatch) {
      if (!map.fields[scoreMatch[1]]) map.fields[scoreMatch[1]] = {};
      map.fields[scoreMatch[1]].score = i;
    } else if (h === '外部連携得点') {
      map.extras.external = i;
    } else if (h === '総計点') {
      map.extras.total = i;
    } else if (h.indexOf('_得点') > -1) {
      map.extras[h] = i;
    }
  }
  return map;
}

function mapResultRow(oldHeaders, oldRow, newHeaders) {
  var newRow = new Array(newHeaders.length).fill('');
  for (var i = 0; i < oldHeaders.length; i++) {
    var idx = newHeaders.indexOf(oldHeaders[i]);
    if (idx >= 0) newRow[idx] = oldRow[i];
  }
  return newRow;
}

function columnIndexToLetter(column) {
  var letter = '';
  while (column > 0) {
    var mod = (column - 1) % 26;
    letter = String.fromCharCode(65 + mod) + letter;
    column = Math.floor((column - 1) / 26);
  }
  return letter;
}

function getTestInfoValue(ss, key) {
  var sheet = ss.getSheetByName(SHEET_TEST_INFO);
  var data = sheet.getDataRange().getValues();
  for (var i = 0; i < data.length; i++) {
    if (data[i][0] === key) return data[i][1] != null ? String(data[i][1]) : '';
  }
  return '';
}

function setTestInfoValue(ss, key, value) {
  var sheet = ss.getSheetByName(SHEET_TEST_INFO);
  var data = sheet.getDataRange().getValues();
  for (var i = 0; i < data.length; i++) {
    if (data[i][0] === key) {
      sheet.getRange(i + 1, 2).setValue(value);
      return;
    }
  }
  sheet.appendRow([key, value]);
}

function getTestInfoObject(ss) {
  var obj = {};
  TEST_INFO_KEYS.forEach(function(k) {
    obj[k] = getTestInfoValue(ss, k);
  });
  return obj;
}
