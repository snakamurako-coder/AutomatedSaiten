/**
 * 模範解答ベース自動採点システム — サーバー側（code.gs）
 */

var HUB_SS_ID_KEY = 'HUB_SS_ID';

function doGet(e) {
  try {
    applyHubIdFromRequest_(e);
    initializeHub();
  } catch (err) {
    return HtmlService.createHtmlOutput(
      '<div style="font-family:sans-serif;padding:2em;max-width:640px">' +
      '<h2>初期設定が必要です</h2>' +
      '<p>' + err.message + '</p>' +
      '<p>ハブ用スプレッドシートを開き、メニュー「自動採点 → Webアプリを開く」（または「ハブを登録」）を実行してから再度アクセスしてください。</p>' +
      '</div>'
    ).setTitle('模範解答ベース自動採点システム');
  }
  return HtmlService.createHtmlOutputFromFile('index')
    .setTitle('模範解答ベース自動採点システム')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

function applyHubIdFromRequest_(e) {
  if (!e || !e.parameter) return;
  var hubId = e.parameter.hubId || e.parameter.hubSsId;
  if (!hubId) return;
  PropertiesService.getScriptProperties().setProperty(HUB_SS_ID_KEY, hubId);
  try {
    setupHubSheets(SpreadsheetApp.openById(hubId));
  } catch (err) { /* hubId invalid */ }
}

function onOpen() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  if (ss) {
    PropertiesService.getScriptProperties().setProperty(HUB_SS_ID_KEY, ss.getId());
    setupHubSheets(ss);
    try { syncHubTestList(); } catch (e) { /* ignore on first open */ }
  }
  SpreadsheetApp.getUi()
    .createMenu('自動採点')
    .addItem('Webアプリを開く', 'openWebAppFromMenu')
    .addItem('ハブを登録', 'registerHubSpreadsheet')
    .addItem('テスト一覧を再同期', 'syncHubTestListFromMenu')
    .addItem('古いWARP設定を削除', 'cleanupWarpScriptProperties')
    .addToUi();
}

function openWebAppFromMenu() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  if (!ss) throw new Error('スプレッドシートを開いた状態で実行してください。');
  PropertiesService.getScriptProperties().setProperty(HUB_SS_ID_KEY, ss.getId());
  setupHubSheets(ss);
  syncHubTestList();
  var url = ScriptApp.getService().getUrl();
  if (!url) {
    SpreadsheetApp.getUi().alert('Webアプリが未デプロイです。「デプロイ」→「新しいデプロイ」で Web アプリを公開してください。');
    return;
  }
  var sep = url.indexOf('?') >= 0 ? '&' : '?';
  var openUrl = url + sep + 'hubId=' + encodeURIComponent(ss.getId());
  var html = HtmlService.createHtmlOutput(
    '<p style="font-family:sans-serif;font-size:13px">アプリを開いています…</p>' +
    '<script>window.open("' + openUrl + '","_blank");google.script.host.close();</script>'
  ).setWidth(260).setHeight(90);
  SpreadsheetApp.getUi().showModalDialog(html, '自動採点アプリ');
}

function syncHubTestListFromMenu() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  if (!ss) throw new Error('スプレッドシートを開いた状態で実行してください。');
  PropertiesService.getScriptProperties().setProperty(HUB_SS_ID_KEY, ss.getId());
  setupHubSheets(ss);
  var n = syncHubTestList();
  SpreadsheetApp.getUi().alert('テスト一覧を再同期しました（' + n + ' 件）。');
}

function registerHubSpreadsheet() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  if (!ss) throw new Error('スプレッドシートを開いた状態で実行してください。');
  PropertiesService.getScriptProperties().setProperty(HUB_SS_ID_KEY, ss.getId());
  setupHubSheets(ss);
  initializeHub();
  var n = syncHubTestList();
  SpreadsheetApp.getUi().alert('ハブを登録しました。\n' + ss.getUrl() + '\n\nテスト一覧: ' + n + ' 件を同期');
  return { hubSsId: ss.getId(), url: ss.getUrl() };
}

function getHubSs() {
  var props = PropertiesService.getScriptProperties();
  var hubId = props.getProperty(HUB_SS_ID_KEY);
  if (hubId) {
    try {
      return SpreadsheetApp.openById(hubId);
    } catch (e) {
      props.deleteProperty(HUB_SS_ID_KEY);
    }
  }
  var active = SpreadsheetApp.getActiveSpreadsheet();
  if (active) {
    props.setProperty(HUB_SS_ID_KEY, active.getId());
    return active;
  }
  throw new Error('ハブ用スプレッドシートが未登録です。スプレッドシートを開き「自動採点 → ハブを登録」を実行してください。');
}

function initializeHub() {
  var properties = PropertiesService.getScriptProperties();
  var ss = getHubSs();
  setupHubSheets(ss);

  var rootFolderId = properties.getProperty('ROOT_IMAGE_FOLDER_ID');
  if (rootFolderId) {
    try {
      DriveApp.getFolderById(rootFolderId);
      return;
    } catch (e) { /* recreate */ }
  }

  var file = DriveApp.getFileById(ss.getId());
  var parents = file.getParents();
  if (!parents.hasNext()) throw new Error('親フォルダの取得に失敗しました。');

  var parentFolder = parents.next();
  var subFolders = parentFolder.getFoldersByName('採点システム画像');
  var rootFolder = subFolders.hasNext() ? subFolders.next() : parentFolder.createFolder('採点システム画像');
  properties.setProperty('ROOT_IMAGE_FOLDER_ID', rootFolder.getId());
}

function setupHubSheets(ss) {
  if (!ss.getSheetByName(SHEET_HUB_TEST_LIST)) {
    var sheet = ss.insertSheet(SHEET_HUB_TEST_LIST);
    sheet.appendRow(['テスト名', 'スプレッドシートID', 'URL', '作成日', 'ステータス', '現在ステップ', '最終保存日時']);
    sheet.setFrozenRows(1);
  } else {
    ensureHubSheetColumns(ss.getSheetByName(SHEET_HUB_TEST_LIST));
  }
  initHubRosterSheet_(ss);
  var sheet1 = ss.getSheetByName('シート1');
  if (sheet1 && ss.getSheets().length > 1 && sheet1.getLastRow() === 0) {
    ss.deleteSheet(sheet1);
  }
}

function getActiveTestSs() {
  var id = PropertiesService.getScriptProperties().getProperty('ACTIVE_TEST_SS_ID');
  if (!id) throw new Error('アクティブなテストが選択されていません。テストを作成または選択してください。');
  return SpreadsheetApp.openById(id);
}

function getActiveTestSsId() {
  return PropertiesService.getScriptProperties().getProperty('ACTIVE_TEST_SS_ID') || '';
}

function getTestImageRootFolder() {
  var properties = PropertiesService.getScriptProperties();
  var rootId = properties.getProperty('ROOT_IMAGE_FOLDER_ID');
  if (!rootId) {
    initializeHub();
    rootId = properties.getProperty('ROOT_IMAGE_FOLDER_ID');
  }
  return DriveApp.getFolderById(rootId);
}

function getOrCreateTestImageFolder(ss) {
  var folderId = getTestInfoValue(ss, '生徒解答フォルダID');
  if (folderId) {
    try {
      return DriveApp.getFolderById(folderId);
    } catch (e) { /* recreate */ }
  }
  var testName = getTestInfoValue(ss, 'テスト名') || ss.getName();
  var root = getTestImageRootFolder();
  var sub = root.createFolder(testName + '_' + ss.getId().substring(0, 8));
  setTestInfoValue(ss, '生徒解答フォルダID', sub.getId());
  return sub;
}

function getOrCreateFeedbackFolder_(ss) {
  ss = ss || getActiveTestSs();
  var parent = getOrCreateTestImageFolder(ss);
  var subs = parent.getFoldersByName(FEEDBACK_FOLDER_NAME);
  if (subs.hasNext()) return subs.next();
  return parent.createFolder(FEEDBACK_FOLDER_NAME);
}




// ========== SheetBuilder.gs ==========

/**
 * シート名定数・テスト用スプレッドシート構築
 */

var SHEET_HUB_TEST_LIST = 'テスト一覧';
var SHEET_ROSTER = '名簿';
var ROSTER_HEADERS = ['名簿名', 'ID', '年', '組', '番号', '氏名', 'その他属性1', 'その他属性2', 'その他属性3'];
var ORIGINAL_ARCHIVE_FOLDER_NAME = '元画像';
var PROCESSED_ARCHIVE_FOLDER_NAME = '処理済み';
var FEEDBACK_FOLDER_NAME = '個票';

var SHEET_TEST_INFO = 'テスト情報';
var SHEET_ANSWER_FIELDS = '記述欄情報';
var SHEET_POINTS = '配点情報';
var SHEET_RESULTS = '採点結果';
var SHEET_CRITERIA = '採点基準';
var SHEET_SUMMARY = '考査総括';
var SHEET_DOMAINS = '領域設定';
var SHEET_IDENTITY_FIELDS = '本人確認欄情報';
var SHEET_EXTERNAL_SCORES = '外部連携得点';
var SHEET_OCR_REPLACEMENTS = 'OCR置換ルール';
var SHEET_DEEMED_SCORING = 'みなし採点';
var SHEET_DEEMED_DRAFT = 'みなし採点下書き';
var SHEET_OUTPUT_SLOTS = '出力欄設定';

var TEST_INFO_KEYS = [
  'テスト名', '科目名', '実施日時', '作成日時',
  '模範解答画像FileID', '生徒解答フォルダID',
  '基準画像幅', '基準画像高さ', 'ステータス',
  '現在ステップ', '最終保存日時', '選択名簿名', 'IDマーク欄使用', '未受験者'
];

var HUB_TEST_LIST_HEADERS = ['テスト名', 'スプレッドシートID', 'URL', '作成日', 'ステータス', '現在ステップ', '最終保存日時'];

function ensureHubSheetColumns(sheet) {
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  HUB_TEST_LIST_HEADERS.forEach(function(h) {
    if (headers.indexOf(h) < 0) {
      sheet.getRange(1, headers.length + 1).setValue(h);
      headers.push(h);
    }
  });
}

function ensureTestInfoKeys(ss) {
  TEST_INFO_KEYS.forEach(function(key) {
    if (getTestInfoValue(ss, key) === '' && key !== 'テスト名') {
      var sheet = ss.getSheetByName(SHEET_TEST_INFO);
      var found = false;
      var data = sheet.getDataRange().getValues();
      for (var i = 0; i < data.length; i++) {
        if (data[i][0] === key) { found = true; break; }
      }
      if (!found) sheet.appendRow([key, '']);
    }
  });
}

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
  ensureOutputSlotsSheet(ss);
  initExternalScoresSheet(ss.getSheetByName(SHEET_EXTERNAL_SCORES));
  ensureOcrReplacementsSheet(ss);
  ensureDeemedScoringSheet(ss);
  ensureDeemedDraftSheet(ss);

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
    EXTERNAL_SCORES: SHEET_EXTERNAL_SCORES,
    OCR_REPLACEMENTS: SHEET_OCR_REPLACEMENTS,
    DEEMED_SCORING: SHEET_DEEMED_SCORING,
    DEEMED_DRAFT: SHEET_DEEMED_DRAFT,
    OUTPUT_SLOTS: SHEET_OUTPUT_SLOTS
  };
}

function ensureCriteriaSheet(ss) {
  var sheet = ss.getSheetByName(SHEET_CRITERIA);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_CRITERIA);
  }
  initCriteriaSheet(sheet);
  return sheet;
}

function ensureOcrReplacementsSheet(ss) {
  if (!ss.getSheetByName(SHEET_OCR_REPLACEMENTS)) {
    var sheet = ss.insertSheet(SHEET_OCR_REPLACEMENTS);
    sheet.appendRow(['記述欄ID', '検索文字列', '置換後', '正規表現']);
    sheet.setFrozenRows(1);
  }
}

function initOcrReplacementsSheet(sheet) {
  if (sheet.getLastRow() > 0) return;
  sheet.appendRow(['記述欄ID', '検索文字列', '置換後', '正規表現']);
  sheet.setFrozenRows(1);
}

function ensureDeemedScoringSheet(ss) {
  if (!ss.getSheetByName(SHEET_DEEMED_SCORING)) {
    var sheet = ss.insertSheet(SHEET_DEEMED_SCORING);
    sheet.appendRow(['記述欄ID', '正答例', '元解答', '適用日時']);
    sheet.setFrozenRows(1);
  }
}

function ensureDeemedDraftSheet(ss) {
  if (!ss.getSheetByName(SHEET_DEEMED_DRAFT)) {
    var sheet = ss.insertSheet(SHEET_DEEMED_DRAFT);
    sheet.appendRow(['記述欄ID', '正答例', '元解答']);
    sheet.setFrozenRows(1);
  }
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
  sheet.appendRow(['記述欄ID', '表示名', 'x', 'y', 'width', 'height', '表示順', 'OCR言語']);
  sheet.setFrozenRows(1);
}

function normalizeOcrLang_(value) {
  var v = String(value || 'en').trim().toLowerCase();
  return v === 'ja' ? 'ja' : 'en';
}

function ensureAnswerFieldsOcrLangColumn_(sheet) {
  if (sheet.getLastRow() === 0) {
    initAnswerFieldsSheet(sheet);
    return;
  }
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  if (headers.indexOf('OCR言語') >= 0) return;
  sheet.getRange(1, sheet.getLastColumn() + 1).setValue('OCR言語');
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

function ensureOutputSlotsSheet(ss) {
  if (!ss.getSheetByName(SHEET_OUTPUT_SLOTS)) {
    var sheet = ss.insertSheet(SHEET_OUTPUT_SLOTS);
    initOutputSlotsSheet(sheet);
  }
}

function initOutputSlotsSheet(sheet) {
  if (sheet.getLastRow() > 0) return;
  sheet.appendRow(['slotKey', 'x', 'y', 'width', 'height', 'printMode']);
  sheet.setFrozenRows(1);
}

function initExternalScoresSheet(sheet) {
  if (sheet.getLastRow() > 0) return;
  sheet.appendRow(['生徒ID', '外部得点', 'ソース', 'インポート日時']);
  sheet.setFrozenRows(1);
}

function buildResultHeaders(fields, extraColumns) {
  var headers = ['生徒ID', 'ファイル名', 'ファイルID', '補正画像FileID', '氏名'];
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
    warpedFileId: headers.indexOf('補正画像FileID'),
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


// ========== TestManager.gs ==========

/**
 * テスト作成・選択・一覧
 */

function createTest(testName, subject, dateTime) {
  if (!testName || !String(testName).trim()) {
    throw new Error('テスト名は必須です。');
  }
  testName = String(testName).trim();
  initializeHub();
  var hubSs = getHubSs();
  setupHubSheets(hubSs);

  var ss = SpreadsheetApp.create(testName);
  buildTestSheets(ss);

  setTestInfoValue(ss, 'テスト名', testName);
  setTestInfoValue(ss, '科目名', subject || '');
  setTestInfoValue(ss, '実施日時', dateTime || '');
  setTestInfoValue(ss, '作成日時', Utilities.formatDate(new Date(), 'JST', 'yyyy-MM-dd HH:mm:ss'));
  setTestInfoValue(ss, 'ステータス', '作成中');
  setTestInfoValue(ss, 'IDマーク欄使用', 'true');

  var folder = getOrCreateTestImageFolder(ss);
  setTestInfoValue(ss, '生徒解答フォルダID', folder.getId());

  var hubFile = DriveApp.getFileById(hubSs.getId());
  var parents = hubFile.getParents();
  if (parents.hasNext()) {
    DriveApp.getFileById(ss.getId()).moveTo(parents.next());
  }

  ensureHubSheetColumns(hubSs.getSheetByName(SHEET_HUB_TEST_LIST));
  hubSs.getSheetByName(SHEET_HUB_TEST_LIST).appendRow([
    testName, ss.getId(), ss.getUrl(),
    Utilities.formatDate(new Date(), 'JST', 'yyyy-MM-dd HH:mm:ss'),
    '作成中', '0', ''
  ]);
  setTestInfoValue(ss, '現在ステップ', '0');

  PropertiesService.getScriptProperties().setProperty('ACTIVE_TEST_SS_ID', ss.getId());

  return {
    testSsId: ss.getId(),
    url: ss.getUrl(),
    testName: testName,
    folderId: folder.getId()
  };
}

function cellToClientString_(value) {
  if (value == null || value === '') return '';
  if (value instanceof Date) {
    return Utilities.formatDate(value, 'JST', 'yyyy-MM-dd HH:mm:ss');
  }
  return String(value);
}

function serializeForClient_(value) {
  if (value == null) return value;
  if (value instanceof Date) return cellToClientString_(value);
  if (Array.isArray(value)) return value.map(serializeForClient_);
  if (typeof value === 'object') {
    var out = {};
    Object.keys(value).forEach(function(k) {
      out[k] = serializeForClient_(value[k]);
    });
    return out;
  }
  return value;
}

function listTestsFromHubSheet_(limit) {
  var hubSs = getHubSs();
  setupHubSheets(hubSs);
  var sheet = hubSs.getSheetByName(SHEET_HUB_TEST_LIST);
  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return [];

  var headers = data[0];
  var colStep = headers.indexOf('現在ステップ');
  var colSaved = headers.indexOf('最終保存日時');
  var activeId = getActiveTestSsId();
  var list = [];
  for (var i = 1; i < data.length; i++) {
    if (!data[i][1]) continue;
    list.push({
      testName: cellToClientString_(data[i][0]),
      testSsId: cellToClientString_(data[i][1]),
      url: cellToClientString_(data[i][2]),
      createdAt: cellToClientString_(data[i][3]),
      status: cellToClientString_(data[i][4]),
      currentStep: colStep >= 0 ? cellToClientString_(data[i][colStep]) : '',
      lastSavedAt: colSaved >= 0 ? cellToClientString_(data[i][colSaved]) : '',
      isActive: cellToClientString_(data[i][1]) === activeId
    });
  }
  list.sort(function(a, b) {
    var da = a.lastSavedAt || a.createdAt || '';
    var db = b.lastSavedAt || b.createdAt || '';
    return db.localeCompare(da);
  });
  if (limit && limit > 0) list = list.slice(0, limit);
  return list;
}

function listTests(limit) {
  initializeHub();
  syncHubTestList();
  return listTestsFromHubSheet_(limit);
}

function getRecentTests(limit) {
  initializeHub();
  return listTestsFromHubSheet_(limit || 20);
}

function getAppBootstrap() {
  try {
    initializeHub();
    var hubSs = getHubSs();
    var tests = listTestsFromHubSheet_(50);
    return serializeForClient_({
      ok: true,
      hubSsId: hubSs.getId(),
      hubUrl: hubSs.getUrl(),
      hubName: hubSs.getName(),
      activeTestSsId: getActiveTestSsId(),
      tests: tests
    });
  } catch (e) {
    return serializeForClient_({
      ok: false,
      error: e.message || String(e),
      hubSsId: '',
      hubUrl: '',
      hubName: '',
      activeTestSsId: getActiveTestSsId(),
      tests: []
    });
  }
}

function getHubParentFolder_(hubSs) {
  var hubFile = DriveApp.getFileById(hubSs.getId());
  var parents = hubFile.getParents();
  return parents.hasNext() ? parents.next() : null;
}

function buildTestListEntryFromSs(ss) {
  ensureTestInfoKeys(ss);
  return {
    testName: getTestInfoValue(ss, 'テスト名') || ss.getName(),
    testSsId: ss.getId(),
    url: ss.getUrl(),
    createdAt: getTestInfoValue(ss, '作成日時') || Utilities.formatDate(new Date(), 'JST', 'yyyy-MM-dd HH:mm:ss'),
    status: getTestInfoValue(ss, 'ステータス') || '作業中',
    currentStep: getTestInfoValue(ss, '現在ステップ') || '0',
    lastSavedAt: getTestInfoValue(ss, '最終保存日時') || ''
  };
}

function discoverTestSpreadsheetsInHubFolder(hubSs) {
  var hubId = hubSs.getId();
  var folder = getHubParentFolder_(hubSs);
  if (!folder) return [];

  var list = [];
  var files = folder.getFilesByType(MimeType.GOOGLE_SHEETS);
  while (files.hasNext()) {
    var file = files.next();
    if (file.getId() === hubId) continue;
    try {
      var ss = SpreadsheetApp.openById(file.getId());
      if (!ss.getSheetByName(SHEET_TEST_INFO)) continue;
      list.push(buildTestListEntryFromSs(ss));
    } catch (err) { /* skip */ }
  }
  return list;
}

function appendHubTestRow(sheet, entry) {
  ensureHubSheetColumns(sheet);
  sheet.appendRow([
    entry.testName,
    entry.testSsId,
    entry.url,
    entry.createdAt,
    entry.status || '作業中',
    entry.currentStep != null ? String(entry.currentStep) : '0',
    entry.lastSavedAt || ''
  ]);
}

function syncHubTestRowFromTestInfo(sheet, rowNum, entry) {
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colStep = headers.indexOf('現在ステップ');
  var colSaved = headers.indexOf('最終保存日時');
  var colStatus = headers.indexOf('ステータス');
  var colName = headers.indexOf('テスト名');
  if (colName >= 0 && entry.testName) sheet.getRange(rowNum, colName + 1).setValue(entry.testName);
  if (colStep >= 0 && entry.currentStep !== '') sheet.getRange(rowNum, colStep + 1).setValue(String(entry.currentStep));
  if (colSaved >= 0 && entry.lastSavedAt) sheet.getRange(rowNum, colSaved + 1).setValue(entry.lastSavedAt);
  if (colStatus >= 0 && entry.status) sheet.getRange(rowNum, colStatus + 1).setValue(entry.status);
}

function syncHubTestList() {
  var hubSs = getHubSs();
  setupHubSheets(hubSs);
  var sheet = hubSs.getSheetByName(SHEET_HUB_TEST_LIST);
  ensureHubSheetColumns(sheet);

  var data = sheet.getDataRange().getValues();
  var existing = {};
  for (var i = 1; i < data.length; i++) {
    if (data[i][1]) existing[String(data[i][1])] = i + 1;
  }

  var discovered = discoverTestSpreadsheetsInHubFolder(hubSs);
  discovered.forEach(function(entry) {
    var rowNum = existing[entry.testSsId];
    if (rowNum) {
      syncHubTestRowFromTestInfo(sheet, rowNum, entry);
    } else {
      appendHubTestRow(sheet, entry);
      existing[entry.testSsId] = sheet.getLastRow();
    }
  });

  return discovered.length;
}

function setActiveTestIdOnly(testSsId) {
  if (!testSsId) throw new Error('テストIDが指定されていません。');
  SpreadsheetApp.openById(testSsId);
  PropertiesService.getScriptProperties().setProperty('ACTIVE_TEST_SS_ID', testSsId);
  return true;
}

function setActiveTest(testSsId) {
  setActiveTestIdOnly(testSsId);
  return serializeForClient_(getTestRestoreData(testSsId));
}

function getTestRestoreSnapshot(testSsId) {
  if (!testSsId) throw new Error('テストIDが指定されていません。');
  return serializeForClient_(getTestRestoreData(testSsId));
}

function getBatchTestRestoreSnapshots(testSsIds) {
  var out = {};
  (testSsIds || []).forEach(function(id) {
    if (!id) return;
    try {
      out[id] = serializeForClient_(getTestRestoreData(id));
    } catch (e) { /* skip inaccessible test */ }
  });
  return out;
}

function touchTestProgress_(ss, stepNum) {
  var current = parseInt(getTestInfoValue(ss, '現在ステップ'), 10) || 0;
  if (stepNum <= current) return;
  var now = Utilities.formatDate(new Date(), 'JST', 'yyyy-MM-dd HH:mm:ss');
  setTestInfoValue(ss, '現在ステップ', String(stepNum));
  setTestInfoValue(ss, '最終保存日時', now);
  updateHubTestProgress(ss.getId(), stepNum, now);
}

function hasGradedResults_(ss) {
  var summary = ss.getSheetByName(SHEET_SUMMARY);
  if (summary && summary.getLastRow() > 1) return true;
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (!sheet || sheet.getLastRow() <= 1) return false;
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);
  var sampleRows = Math.min(sheet.getLastRow() - 1, 10);
  if (sampleRows <= 0) return false;
  var data = sheet.getRange(2, 1, sampleRows, sheet.getLastColumn()).getValues();
  for (var r = 0; r < data.length; r++) {
    var labels = Object.keys(colMap.fields);
    for (var i = 0; i < labels.length; i++) {
      var jIdx = colMap.fields[labels[i]].judgment;
      if (jIdx >= 0 && data[r][jIdx]) return true;
    }
  }
  return false;
}

function inferCompletedSteps_(ss) {
  var list = [0];
  var fields = getAnswerFields(ss);
  if (fields.length && getTestInfoValue(ss, '模範解答画像FileID')) list.push(1);
  if (Object.keys(getPointsMap(ss)).length > 0) list.push(2);
  var results = ss.getSheetByName(SHEET_RESULTS);
  if (results && results.getLastRow() > 1) list.push(3);
  if (getGradingCriteria(ss).length > 0) list.push(4);
  if (hasGradedResults_(ss)) list.push(5);
  var domains = getDomainSettings(ss);
  if (domains.some(function(d) { return d.daiMon || d.hanI || d.noryoku; })) list.push(6);
  var extSheet = ss.getSheetByName(SHEET_EXTERNAL_SCORES);
  if (extSheet && extSheet.getLastRow() > 1) list.push(7);
  if (getIdentityFields(ss).length > 0) list.push(8);
  var maxStep = 0;
  list.forEach(function(s) { if (s > maxStep) maxStep = s; });
  return { list: list, maxStep: maxStep };
}

function getDomainSettingsForUiFromSs(ss) {
  var fields = getAnswerFields(ss);
  var domains = getDomainSettings(ss);
  var domainMap = {};
  domains.forEach(function(d) { domainMap[d.fieldId] = d; });
  return fields.map(function(f) {
    var d = domainMap[f.id] || {};
    return {
      fieldId: f.id,
      displayName: f.displayName || f.id,
      daiMon: d.daiMon || '',
      hanI: d.hanI || '',
      noryoku: d.noryoku || ''
    };
  });
}

function getCriteriaGroupedByField_(ss) {
  var rules = getGradingCriteria(ss);
  var grouped = {};
  rules.forEach(function(r) {
    var key = String(r.fieldId);
    if (!grouped[key]) grouped[key] = [];
    grouped[key].push(r);
  });
  return grouped;
}

function getSummaryDataFromSs(ss) {
  var sheet = ss.getSheetByName(SHEET_SUMMARY);
  if (!sheet || sheet.getLastRow() <= 1) return [];
  return sheet.getRange(2, 1, sheet.getLastRow() - 1, 4).getValues().map(function(row) {
    return { category: row[0], item: row[1], value: row[2], note: row[3] };
  });
}

function getExternalScoresFromSs(ss) {
  var sheet = ss.getSheetByName(SHEET_EXTERNAL_SCORES);
  if (!sheet || sheet.getLastRow() <= 1) return [];
  var data = sheet.getDataRange().getValues();
  var list = [];
  for (var i = 1; i < data.length; i++) {
    list.push({
      studentId: String(data[i][0]),
      score: parseFloat(data[i][1]) || 0,
      source: String(data[i][2] || ''),
      importedAt: data[i][3]
    });
  }
  return list;
}

function getOcrResultPreview_(ss) {
  var fields = getAnswerFields(ss);
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (!sheet || sheet.getLastRow() <= 1) return [];
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);
  var data = sheet.getRange(2, 1, sheet.getLastRow() - 1, sheet.getLastColumn()).getValues();
  return data.map(function(row) {
    var textMapping = {};
    fields.forEach(function(f) {
      var label = f.displayName || f.id;
      var fm = colMap.fields[label];
      if (fm && fm.text >= 0) textMapping[f.id] = String(row[fm.text] || '');
    });
    return {
      fileId: colMap.fileId >= 0 ? String(row[colMap.fileId] || '') : '',
      fileName: colMap.fileName >= 0 ? String(row[colMap.fileName] || '') : '',
      studentId: colMap.studentId >= 0 ? String(row[colMap.studentId] || '') : '',
      textMapping: textMapping
    };
  });
}

function getBatchRestoreSnapshot_(ss) {
  var folderId = getTestInfoValue(ss, '生徒解答フォルダID');
  var processedIds = Object.keys(getProcessedFileIds(ss));
  var processedNames = Object.keys(getProcessedFileNames(ss));
  var workQueue = buildOcrWorkQueue_(ss);
  var files = [];
  if (folderId) {
    try {
      files = listFolderFiles(folderId);
    } catch (e) { /* folder inaccessible */ }
  }
  return {
    folderId: folderId,
    files: files,
    processedFileIds: processedIds,
    processedFileNames: processedNames,
    workQueueStats: workQueue.stats,
    resultPreview: getOcrResultPreview_(ss),
    processedCount: processedNames.length,
    totalFiles: files.length
  };
}

function getTestRestoreData(testSsId) {
  var ss = testSsId ? SpreadsheetApp.openById(testSsId) : getActiveTestSs();
  ensureTestInfoKeys(ss);
  ensureCriteriaSheet(ss);
  ensureOcrReplacementsSheet(ss);
  ensureDeemedScoringSheet(ss);
  ensureDeemedDraftSheet(ss);
  ensureOutputSlotsSheet(ss);
  var completed = inferCompletedSteps_(ss);
  var savedStep = parseInt(getTestInfoValue(ss, '現在ステップ'), 10) || 0;
  var resumeStep = savedStep > 0 ? savedStep : completed.maxStep;

  return {
    testSsId: ss.getId(),
    url: ss.getUrl(),
    info: getTestInfoObject(ss),
    fields: getAnswerFields(ss),
    points: getPointsMap(ss),
    identityFields: getIdentityFields(ss),
    domainSettings: getDomainSettingsForUiFromSs(ss),
    criteriaByField: getCriteriaGroupedByField_(ss),
    summary: getSummaryDataFromSs(ss),
    externalScores: getExternalScoresFromSs(ss),
    batchRestore: getBatchRestoreSnapshot_(ss),
    resultRowCount: Math.max(0, (ss.getSheetByName(SHEET_RESULTS).getLastRow() || 1) - 1),
    completedSteps: completed.list,
    currentStep: resumeStep,
    lastSavedAt: getTestInfoValue(ss, '最終保存日時'),
    ocrReplacementsByField: getOcrReplacementsGrouped_(ss),
    deemedScoringByField: getDeemedScoringGrouped_(ss),
    deemedDraftByField: getDeemedDraftGrouped_(ss),
    outputSlots: getOutputSlots(ss),
    availableOutputSlotKeys: getAvailableOutputSlotKeys_(ss),
    activeTestSsId: ss.getId()
  };
}

function getOcrReplacementsGrouped_(ss) {
  ensureOcrReplacementsSheet(ss);
  var all = getOcrReplacementsForSs(ss, null);
  var grouped = {};
  all.forEach(function(r) {
    if (!grouped[r.fieldId]) grouped[r.fieldId] = [];
    grouped[r.fieldId].push(r);
  });
  return grouped;
}

function getTestInfo(testSsId) {
  return getTestRestoreData(testSsId);
}

function updateHubTestProgress(testSsId, stepNum, savedAt) {
  var hubSs = getHubSs();
  var sheet = hubSs.getSheetByName(SHEET_HUB_TEST_LIST);
  ensureHubSheetColumns(sheet);
  var data = sheet.getDataRange().getValues();
  var headers = data[0];
  var colStep = headers.indexOf('現在ステップ');
  var colSaved = headers.indexOf('最終保存日時');
  for (var i = 1; i < data.length; i++) {
    if (data[i][1] === testSsId) {
      if (colStep >= 0) sheet.getRange(i + 1, colStep + 1).setValue(String(stepNum));
      if (colSaved >= 0) sheet.getRange(i + 1, colSaved + 1).setValue(savedAt);
      if (headers.indexOf('ステータス') >= 0) sheet.getRange(i + 1, headers.indexOf('ステータス') + 1).setValue('作業中');
      return;
    }
  }
  try {
    var ss = SpreadsheetApp.openById(testSsId);
    appendHubTestRow(sheet, buildTestListEntryFromSs(ss));
    var lastRow = sheet.getLastRow();
    if (colStep >= 0) sheet.getRange(lastRow, colStep + 1).setValue(String(stepNum));
    if (colSaved >= 0) sheet.getRange(lastRow, colSaved + 1).setValue(savedAt);
  } catch (e) { /* ignore */ }
}

function saveStepProgress(stepNum, clientPayload) {
  var ss = getActiveTestSs();
  ensureTestInfoKeys(ss);
  var now = Utilities.formatDate(new Date(), 'JST', 'yyyy-MM-dd HH:mm:ss');
  setTestInfoValue(ss, '現在ステップ', String(stepNum));
  setTestInfoValue(ss, '最終保存日時', now);
  updateHubTestProgress(ss.getId(), stepNum, now);

  if (stepNum === 1 && clientPayload && clientPayload.fields) {
    saveAnswerFields(clientPayload.fields);
    if (clientPayload.modelBase64) {
      saveModelAnswerImage(clientPayload.modelBase64, clientPayload.width, clientPayload.height);
    }
    if (clientPayload.useIdMark != null) {
      setTestInfoValue(ss, 'IDマーク欄使用', clientPayload.useIdMark ? 'true' : 'false');
    }
  } else if (stepNum === 2 && clientPayload && clientPayload.points) {
    savePoints(clientPayload.points);
  } else if (stepNum === 3 && clientPayload && clientPayload.folderId) {
    setTestInfoValue(ss, '生徒解答フォルダID', clientPayload.folderId);
  } else if (stepNum === 7 && clientPayload) {
    if (clientPayload.rosterName != null) {
      setTestInfoValue(ss, '選択名簿名', clientPayload.rosterName || '');
    }
    if (clientPayload.absentStudents) {
      saveRosterAbsentState_(ss, clientPayload.rosterName || getTestInfoValue(ss, '選択名簿名'), clientPayload.absentStudents);
    } else if (clientPayload.absentIndices) {
      saveRosterAbsentState_(ss, clientPayload.rosterName || getTestInfoValue(ss, '選択名簿名'),
        migrateAbsentIndicesToStudents_(clientPayload.rosterName || getTestInfoValue(ss, '選択名簿名'), clientPayload.absentIndices));
    }
  } else if (stepNum === 8 && clientPayload && clientPayload.identityFields) {
    saveIdentityFields(clientPayload.identityFields);
  }
  return { step: stepNum, savedAt: now };
}

function checkVisionApiKey() {
  var key = PropertiesService.getScriptProperties().getProperty('VISION_API_KEY');
  if (!key || !String(key).trim()) {
    return { configured: false, message: 'VISION_API_KEY がスクリプトプロパティに未設定です。Apps Script のプロジェクト設定から追加してください。' };
  }
  return { configured: true, message: 'Vision API キーが設定されています。' };
}

function updateTestStatus(status) {
  var ss = getActiveTestSs();
  setTestInfoValue(ss, 'ステータス', status);
  updateHubTestStatus(ss.getId(), status);
  return true;
}

function updateHubTestStatus(testSsId, status) {
  var hubSs = getHubSs();
  var sheet = hubSs.getSheetByName(SHEET_HUB_TEST_LIST);
  var data = sheet.getDataRange().getValues();
  for (var i = 1; i < data.length; i++) {
    if (data[i][1] === testSsId) {
      sheet.getRange(i + 1, 5).setValue(status);
      return;
    }
  }
}

function saveStudentFolderId(folderId) {
  var ss = getActiveTestSs();
  setTestInfoValue(ss, '生徒解答フォルダID', folderId);
  return true;
}

function saveModelAnswerImage(base64Image, width, height) {
  var ss = getActiveTestSs();
  var imageBytes = base64Image.split(',')[1];
  var folder = getOrCreateTestImageFolder(ss);
  var fileName = '模範解答_' + Utilities.formatDate(new Date(), 'JST', 'yyyyMMdd_HHmmss') + '.jpg';
  var oldId = getTestInfoValue(ss, '模範解答画像FileID');
  if (oldId) {
    try { DriveApp.getFileById(oldId).setTrashed(true); } catch (e) { /* ignore */ }
  }
  var file = folder.createFile(Utilities.newBlob(Utilities.base64Decode(imageBytes), 'image/jpeg', fileName));
  setTestInfoValue(ss, '模範解答画像FileID', file.getId());
  if (width) setTestInfoValue(ss, '基準画像幅', width);
  if (height) setTestInfoValue(ss, '基準画像高さ', height);
  return { fileId: file.getId(), fileName: fileName };
}


// ========== FieldManager.gs ==========

/**
 * 記述欄・本人確認欄・配点の管理
 */

function getAnswerFields(ss) {
  ss = ss || getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_ANSWER_FIELDS);
  ensureAnswerFieldsOcrLangColumn_(sheet);
  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return [];

  var headers = data[0];
  var colLang = headers.indexOf('OCR言語');
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
      order: parseInt(data[i][6], 10) || i,
      ocrLang: colLang >= 0 ? normalizeOcrLang_(data[i][colLang]) : 'en'
    });
  }
  fields.sort(function(a, b) { return a.order - b.order; });
  return fields;
}

function fieldsNeedPerCropOcr_(fields) {
  if (!fields || fields.length <= 1) return false;
  var first = normalizeOcrLang_(fields[0].ocrLang);
  for (var i = 1; i < fields.length; i++) {
    if (normalizeOcrLang_(fields[i].ocrLang) !== first) return true;
  }
  return false;
}

function saveAnswerFields(fields) {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_ANSWER_FIELDS);
  ensureAnswerFieldsOcrLangColumn_(sheet);
  sheet.clear();
  sheet.appendRow(['記述欄ID', '表示名', 'x', 'y', 'width', 'height', '表示順', 'OCR言語']);

  fields.forEach(function(f, idx) {
    sheet.appendRow([
      f.id,
      f.displayName || f.id,
      f.x, f.y, f.width, f.height,
      f.order != null ? f.order : idx + 1,
      normalizeOcrLang_(f.ocrLang)
    ]);
  });

  syncPointsSheet(ss, fields);
  rebuildResultsSheetHeaders(ss);
  touchTestProgress_(ss, 1);
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
  touchTestProgress_(ss, 2);
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
  if (!fields || !fields.length) {
    throw new Error('本人確認欄を1つ以上設定してください。');
  }
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_IDENTITY_FIELDS);
  sheet.clear();
  sheet.appendRow(['欄種別', 'x', 'y', 'width', 'height']);
  fields.forEach(function(f) {
    sheet.appendRow([f.type, f.x, f.y, f.width, f.height]);
  });
  touchTestProgress_(ss, 8);
  return getIdentityFields(ss);
}

function fieldsToBoxes(fields) {
  return fields.map(function(f) {
    return { id: f.id, x: f.x, y: f.y, w: f.width, h: f.height };
  });
}


// ========== DriveService.gs ==========

/**
 * Google Drive ファイル操作
 */

var IMAGE_MIME_TYPES = {
  'image/jpeg': true,
  'image/png': true,
  'image/jpg': true,
  'application/pdf': true
};

function isWarpedOutputFileName_(name) {
  return String(name || '').indexOf('補正_') === 0;
}

/** フォルダ直下の元スキャン（画像/PDF）。補正済み出力（補正_*）とサブフォルダ内は含めない。 */
function listFolderFiles(folderId) {
  if (!folderId) throw new Error('フォルダIDを指定してください。');
  var folder = DriveApp.getFolderById(folderId);
  var files = folder.getFiles();
  var list = [];

  while (files.hasNext()) {
    var file = files.next();
    if (isWarpedOutputFileName_(file.getName())) continue;
    var mime = file.getMimeType();
    if (!IMAGE_MIME_TYPES[mime]) continue;
    list.push({
      id: file.getId(),
      name: file.getName(),
      mimeType: mime,
      isPdf: mime === 'application/pdf'
    });
  }
  list.sort(function(a, b) { return naturalCompareFileNames_(a.name, b.name); });
  return list;
}

function getAssignSortFileName_(fileMeta) {
  if (!fileMeta) return '';
  if (fileMeta.sortFileName) return normalizeResultFileName_(fileMeta.sortFileName);
  if (fileMeta.fileName) return normalizeResultFileName_(fileMeta.fileName);
  if (fileMeta.originalFileName) return normalizeResultFileName_(fileMeta.originalFileName);
  if (fileMeta.name) {
    return normalizeResultFileName_(parseWarpedOriginalFileName_(fileMeta.name) || fileMeta.name);
  }
  return '';
}

function sortByAssignFileNameAsc_(items) {
  var list = (items || []).slice();
  list.sort(function(a, b) {
    return naturalCompareFileNames_(getAssignSortFileName_(a), getAssignSortFileName_(b));
  });
  return list;
}

/** 採点結果シートのデータ行をファイル名列の昇順（自然順）で並べ替え */
function sortResultsSheetByFileNameAsc_(ss) {
  ss = ss || getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (!sheet || sheet.getLastRow() <= 1) return 0;
  var lastRow = sheet.getLastRow();
  var numCols = sheet.getLastColumn();
  var headers = sheet.getRange(1, 1, 1, numCols).getValues()[0];
  var colMap = getResultColumnMap(headers);
  if (colMap.fileName < 0) return 0;
  var numRows = lastRow - 1;
  if (numRows <= 0) return 0;
  var data = sheet.getRange(2, 1, numRows, numCols).getValues();
  data.sort(function(a, b) {
    var na = normalizeResultFileName_(a[colMap.fileName]);
    var nb = normalizeResultFileName_(b[colMap.fileName]);
    return naturalCompareFileNames_(na, nb);
  });
  sheet.getRange(2, 1, numRows, numCols).setValues(data);
  return data.length;
}

/** フォルダ直下の補正済み画像（補正_*）のみ。サブフォルダは含めない。Step③のキューとは無関係。 */
function listWarpedFilesInFolderDirect_(folderId) {
  if (!folderId) return [];
  var folder = DriveApp.getFolderById(folderId);
  var files = folder.getFiles();
  var list = [];
  while (files.hasNext()) {
    var file = files.next();
    var name = file.getName();
    if (name.indexOf('補正_') !== 0) continue;
    var mime = file.getMimeType();
    if (mime !== 'image/jpeg' && mime !== 'image/png' && mime !== 'image/jpg') continue;
    list.push({
      id: file.getId(),
      name: name,
      mimeType: mime,
      isPdf: false,
      originalFileName: parseWarpedOriginalFileName_(name)
    });
  }
  return sortByAssignFileNameAsc_(list);
}

function listWarpedFilesInFolderDirect(folderId) {
  return listWarpedFilesInFolderDirect_(folderId || getTestInfoValue(getActiveTestSs(), '生徒解答フォルダID'));
}

function getWarpedFileCountForRosterAssign_(ss) {
  ss = ss || getActiveTestSs();
  var folderId = getTestInfoValue(ss, '生徒解答フォルダID');
  if (!folderId) return { count: 0, files: [], folderId: '' };
  try {
    var files = listWarpedFilesInFolderDirect_(folderId);
    return { count: files.length, files: files, folderId: folderId };
  } catch (e) {
    return { count: 0, files: [], folderId: folderId, error: e.toString() };
  }
}

function normalizeAbsentStudentEntry_(entry) {
  if (entry == null) return null;
  var norm = {
    studentId: String(entry.studentId != null ? entry.studentId : (entry.id != null ? entry.id : '')).trim(),
    name: String(entry.name || '').trim()
  };
  if (!norm.studentId && !norm.name) return null;
  return norm;
}

function normalizeAbsentStudentEntries_(entries) {
  var out = [];
  var seen = {};
  (entries || []).forEach(function(e) {
    var norm = normalizeAbsentStudentEntry_(e);
    if (!norm) return;
    var key = (norm.studentId ? 'id:' + norm.studentId : '') + '|' + (norm.name ? 'name:' + norm.name : '');
    if (seen[key]) return;
    seen[key] = true;
    out.push(norm);
  });
  return out;
}

function rosterRowMatchesAbsent_(row, entry) {
  var sid = String(entry.studentId || '').trim();
  var name = String(entry.name || '').trim();
  if (!sid && !name) return false;
  var rowSid = String(row.studentId || '').trim();
  var rowName = String(row.name || '').trim();
  if (sid && rowSid && sid === rowSid) return true;
  if (name && rowName && name === rowName) return true;
  return false;
}

function buildAbsentRowIndexSet_(rosterName, absentStudents) {
  var entries = normalizeAbsentStudentEntries_(absentStudents);
  var rosterAll = getRosterRows(rosterName);
  var absentSet = {};
  rosterAll.forEach(function(r) {
    for (var i = 0; i < entries.length; i++) {
      if (rosterRowMatchesAbsent_(r, entries[i])) {
        absentSet[r.rowIndex] = true;
        break;
      }
    }
  });
  return { absentSet: absentSet, rosterAll: rosterAll, entries: entries };
}

function migrateAbsentIndicesToStudents_(rosterName, absentIndices) {
  var rows = getRosterRows(rosterName);
  return (absentIndices || []).map(function(i) {
    var r = rows[parseInt(i, 10)];
    return r ? normalizeAbsentStudentEntry_({ studentId: r.studentId, name: r.name }) : null;
  }).filter(function(e) { return !!e; });
}

function parseRosterAbsentStateJson_(raw, rosterNameForMigration) {
  if (!raw) return null;
  try {
    var parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
    if (!parsed || typeof parsed !== 'object') return null;
    parsed.rosterName = parsed.rosterName != null ? String(parsed.rosterName) : '';
    if (parsed.absentStudents && parsed.absentStudents.length) {
      parsed.absentStudents = normalizeAbsentStudentEntries_(parsed.absentStudents);
    } else if (parsed.absentIndices && parsed.absentIndices.length) {
      var rn = parsed.rosterName || rosterNameForMigration || '';
      parsed.absentStudents = migrateAbsentIndicesToStudents_(rn, parsed.absentIndices);
    } else {
      parsed.absentStudents = [];
    }
    return parsed;
  } catch (e) {
    return null;
  }
}

function saveRosterAbsentState_(ss, rosterName, absentStudents) {
  ss = ss || getActiveTestSs();
  var payload = {
    rosterName: rosterName || '',
    absentStudents: normalizeAbsentStudentEntries_(absentStudents),
    savedAt: Utilities.formatDate(new Date(), 'JST', 'yyyy-MM-dd HH:mm:ss')
  };
  setTestInfoValue(ss, '未受験者', JSON.stringify(payload));
  if (rosterName) setTestInfoValue(ss, '選択名簿名', rosterName);
  return payload;
}

function getRosterAbsentState() {
  var ss = getActiveTestSs();
  var rosterName = getTestInfoValue(ss, '選択名簿名');
  return parseRosterAbsentStateJson_(getTestInfoValue(ss, '未受験者'), rosterName) ||
    { rosterName: '', absentStudents: [], savedAt: '' };
}

function saveRosterAbsentState(rosterName, absentStudents) {
  return saveRosterAbsentState_(getActiveTestSs(), rosterName, absentStudents);
}

function buildResultRowLookups_(sheet) {
  var rowIndexByFileId = {};
  var rowIndexByFileName = {};
  var rowIndexByWarpedFileId = {};
  if (!sheet || sheet.getLastRow() <= 1) {
    return { rowIndexByFileId: rowIndexByFileId, rowIndexByFileName: rowIndexByFileName, rowIndexByWarpedFileId: rowIndexByWarpedFileId };
  }
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);
  var data = sheet.getRange(2, 1, sheet.getLastRow() - 1, sheet.getLastColumn()).getValues();
  data.forEach(function(row, i) {
    var rowIdx = i + 2;
    var fid = colMap.fileId >= 0 ? String(row[colMap.fileId] || '') : '';
    var fname = colMap.fileName >= 0 ? normalizeResultFileName_(row[colMap.fileName]) : '';
    var wid = colMap.warpedFileId >= 0 ? String(row[colMap.warpedFileId] || '') : '';
    if (fid) rowIndexByFileId[fid] = rowIdx;
    if (fname) rowIndexByFileName[fname] = rowIdx;
    if (wid) rowIndexByWarpedFileId[wid] = rowIdx;
  });
  return { rowIndexByFileId: rowIndexByFileId, rowIndexByFileName: rowIndexByFileName, rowIndexByWarpedFileId: rowIndexByWarpedFileId };
}

function findResultRowForWarpedFile_(warpedFile, lookups) {
  if (!warpedFile) return null;
  var rowIdx = lookups.rowIndexByWarpedFileId[warpedFile.id];
  if (rowIdx) return rowIdx;
  var origName = warpedFile.originalFileName || parseWarpedOriginalFileName_(warpedFile.name);
  if (origName) {
    rowIdx = lookups.rowIndexByFileName[normalizeResultFileName_(origName)];
    if (rowIdx) return rowIdx;
  }
  return null;
}

function naturalCompareFileNames_(nameA, nameB) {
  return String(nameA).localeCompare(String(nameB), 'ja', { numeric: true, sensitivity: 'base' });
}

function getDriveFileBase64(fileId) {
  if (!fileId) throw new Error('ファイルIDが指定されていません。');
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

function readWarpedDataUrlFromDrive_(warpedFileId) {
  var data = getDriveFileBase64(warpedFileId);
  return 'data:image/jpeg;base64,' + data.base64;
}

/** 補正画像のメタ＋必要なら base64 を1回の呼び出しで返す（google.script.run 多重呼び出し回避） */
function loadWarpedImageForOcr(sourceFileId, sourceFileName, warpedFileIdHint, includeBase64) {
  try {
    var ss = getActiveTestSs();
    var warpedId = warpedFileIdHint ? String(warpedFileIdHint) : '';
    if (!warpedId) warpedId = getWarpedFileIdFromResults(ss, sourceFileId);
    if (!warpedId) warpedId = findWarpedFileInFolder_(sourceFileId, sourceFileName);
    if (!warpedId) {
      return { success: false, error: '補正画像が見つかりません。' };
    }
    var studentId = getStudentIdFromResults(ss, sourceFileId);
    var out = {
      success: true,
      warpedFileId: warpedId,
      studentId: studentId,
      fileId: sourceFileId || '',
      fileName: sourceFileName || ''
    };
    if (includeBase64 !== false) {
      var data = getDriveFileBase64(warpedId);
      out.base64 = data.base64;
      out.mimeType = data.mimeType;
    }
    return out;
  } catch (error) {
    return { success: false, error: error.toString() };
  }
}

function saveWarpedImage(base64Image, originalFileName, studentId, sourceFileId) {
  var ss = getActiveTestSs();
  if (sourceFileId) {
    var existing = getWarpedFileIdFromResults(ss, sourceFileId);
    if (!existing) existing = findWarpedFileInFolder_(sourceFileId, originalFileName);
    if (existing) return { fileId: existing, fileName: '', reused: true };
  }
  var folder = getOrCreateTestImageFolder(ss);
  var imageBytes = base64Image.split(',')[1];
  var safeId = studentId && !String(studentId).includes('?') ? studentId : 'unknown';
  var fileName = '補正_' + safeId + '_' + (originalFileName || 'image') + '.jpg';
  fileName = fileName.replace(/[^\w\u3040-\u30ff\u4e00-\u9faf.\-]/g, '_').substring(0, 200);
  var file = folder.createFile(Utilities.newBlob(Utilities.base64Decode(imageBytes), 'image/jpeg', fileName));
  return { fileId: file.getId(), fileName: fileName, reused: false };
}

function getOrCreateOriginalArchiveFolder(studentFolder) {
  var sub = studentFolder.getFoldersByName(ORIGINAL_ARCHIVE_FOLDER_NAME);
  return sub.hasNext() ? sub.next() : studentFolder.createFolder(ORIGINAL_ARCHIVE_FOLDER_NAME);
}

function getOrCreateProcessedFolder_(studentFolder) {
  var sub = studentFolder.getFoldersByName(PROCESSED_ARCHIVE_FOLDER_NAME);
  return sub.hasNext() ? sub.next() : studentFolder.createFolder(PROCESSED_ARCHIVE_FOLDER_NAME);
}

function archiveOriginalFile(fileId, studentFolderId) {
  return moveSourceFileToOriginalArchive_(fileId, '', studentFolderId);
}

/** 採点結果シートへの追記成功後のみ、フォルダ直下の元ファイルを「元画像/」へ退避 */
function moveSourceFileToOriginalArchive_(fileId, fileName, studentFolderId) {
  if (!studentFolderId) return { moved: false };
  try {
    var studentFolder = DriveApp.getFolderById(studentFolderId);
    var archiveFolder = getOrCreateOriginalArchiveFolder(studentFolder);
    var file = null;
    if (fileId) {
      try { file = DriveApp.getFileById(fileId); } catch (e) { /* stale id */ }
    }
    if (!file && fileName) {
      var found = findSourceFileInInbox_(studentFolderId, fileName);
      if (found) {
        try { file = DriveApp.getFileById(found.id); } catch (e) { /* ignore */ }
      }
    }
    if (!file) return { moved: false, reason: 'not_found' };

    var parents = file.getParents();
    while (parents.hasNext()) {
      var parent = parents.next();
      if (parent.getId() === archiveFolder.getId()) {
        return { moved: false, alreadyArchived: true };
      }
      if (parent.getId() === studentFolder.getId()) {
        file.moveTo(archiveFolder);
        return { moved: true, archiveFolderId: archiveFolder.getId() };
      }
    }
  } catch (e) {
    return { moved: false, error: e.toString() };
  }
  return { moved: false, reason: 'wrong_parent' };
}

function findSourceFileInInbox_(studentFolderId, fileName) {
  if (!studentFolderId || !fileName) return null;
  var target = normalizeResultFileName_(fileName);
  var folder = DriveApp.getFolderById(studentFolderId);
  var direct = folder.getFiles();
  while (direct.hasNext()) {
    var f = direct.next();
    if (normalizeResultFileName_(f.getName()) === target) {
      return { id: f.getId(), name: f.getName(), inArchive: false };
    }
  }
  return null;
}

function moveResultRowsToOriginalArchive_(rows, studentFolderId) {
  if (!rows || !rows.length || !studentFolderId) return 0;
  var moved = 0;
  rows.forEach(function(r) {
    var res = moveSourceFileToOriginalArchive_(r.fileId, r.fileName, studentFolderId);
    if (res.moved) moved++;
  });
  return moved;
}

function getStudentIdFromResults(ss, sourceFileId) {
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (sheet.getLastRow() <= 1) return '';
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);
  if (colMap.fileId < 0 || colMap.studentId < 0) return '';
  var data = sheet.getRange(2, 1, sheet.getLastRow() - 1, sheet.getLastColumn()).getValues();
  for (var i = 0; i < data.length; i++) {
    if (String(data[i][colMap.fileId]) === String(sourceFileId)) {
      return String(data[i][colMap.studentId] || '');
    }
  }
  return '';
}

function getExistingWarpedForFile(sourceFileId, sourceFileName) {
  var ss = getActiveTestSs();
  var warpedId = getWarpedFileIdFromResults(ss, sourceFileId);
  if (!warpedId) warpedId = findWarpedFileInFolder_(sourceFileId, sourceFileName);
  if (!warpedId) return null;
  return {
    warpedFileId: warpedId,
    studentId: getStudentIdFromResults(ss, sourceFileId)
  };
}

function findWarpedFileInFolder_(sourceFileId, sourceFileName) {
  try {
    var ss = getActiveTestSs();
    var folder = getOrCreateTestImageFolder(ss);
    var files = folder.getFiles();
    var safeName = String(sourceFileName || '').replace(/[^\w\u3040-\u30ff\u4e00-\u9faf.\-]/g, '_');
    while (files.hasNext()) {
      var f = files.next();
      var name = f.getName();
      if (name.indexOf('補正_') !== 0) continue;
      if (safeName && name.indexOf(safeName) >= 0) return f.getId();
      if (sourceFileId && name.indexOf(sourceFileId) >= 0) return f.getId();
    }
  } catch (e) { /* ignore */ }
  return '';
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

function getProcessedFileNames(ss) {
  ss = ss || getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (sheet.getLastRow() <= 1) return {};
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var fileNameCol = headers.indexOf('ファイル名');
  if (fileNameCol < 0) return {};

  var data = sheet.getRange(2, 1, sheet.getLastRow() - 1, sheet.getLastColumn()).getValues();
  var map = {};
  data.forEach(function(row) {
    var name = normalizeResultFileName_(row[fileNameCol]);
    if (name) map[name] = true;
  });
  return map;
}

function normalizeResultFileName_(name) {
  return String(name || '').trim();
}

function isFileNameInResults_(fileName, processedNames) {
  return !!processedNames[normalizeResultFileName_(fileName)];
}

function parseWarpedOriginalFileName_(warpedFileName) {
  var name = String(warpedFileName || '');
  if (name.indexOf('補正_') !== 0) return '';
  var rest = name.substring(3);
  var dot = rest.lastIndexOf('.');
  if (dot > 0) rest = rest.substring(0, dot);
  var sep = rest.indexOf('_');
  if (sep < 0) return '';
  return rest.substring(sep + 1);
}

function listArchivedSourceFiles_(studentFolderId) {
  if (!studentFolderId) return [];
  try {
    var studentFolder = DriveApp.getFolderById(studentFolderId);
    var archiveFolder = getOrCreateOriginalArchiveFolder(studentFolder);
    var files = archiveFolder.getFiles();
    var list = [];
    while (files.hasNext()) {
      var file = files.next();
      var mime = file.getMimeType();
      if (!IMAGE_MIME_TYPES[mime]) continue;
      list.push({
        id: file.getId(),
        name: file.getName(),
        mimeType: mime,
        isPdf: mime === 'application/pdf',
        inArchive: true
      });
    }
    list.sort(function(a, b) { return naturalCompareFileNames_(a.name, b.name); });
    return list;
  } catch (e) {
    return [];
  }
}

function findSourceFileByName_(studentFolderId, fileName) {
  if (!studentFolderId || !fileName) return null;
  var target = normalizeResultFileName_(fileName);
  var folder = DriveApp.getFolderById(studentFolderId);
  var direct = folder.getFiles();
  while (direct.hasNext()) {
    var f = direct.next();
    if (normalizeResultFileName_(f.getName()) === target) {
      return { id: f.getId(), name: f.getName(), mimeType: f.getMimeType(), isPdf: f.getMimeType() === 'application/pdf', inArchive: false, location: 'inbox' };
    }
  }
  var archived = listArchivedSourceFiles_(studentFolderId);
  for (var i = 0; i < archived.length; i++) {
    if (normalizeResultFileName_(archived[i].name) === target) {
      archived[i].location = 'warp_pending';
      return archived[i];
    }
  }
  return null;
}

function buildOcrWorkQueue_(ss) {
  ss = ss || getActiveTestSs();
  var folderId = getTestInfoValue(ss, '生徒解答フォルダID');
  var processedNames = getProcessedFileNames(ss);
  var itemsByName = {};

  function ensureItem(meta) {
    var key = normalizeResultFileName_(meta.name);
    if (!key || isFileNameInResults_(key, processedNames)) return;
    if (!itemsByName[key]) {
      itemsByName[key] = {
        id: meta.id || '',
        name: meta.name,
        mimeType: meta.mimeType || 'image/jpeg',
        isPdf: !!meta.isPdf,
        stage: 'warp_and_ocr',
        warpedFileId: '',
        inArchive: !!meta.inArchive
      };
    } else {
      if (meta.id && !itemsByName[key].id) itemsByName[key].id = meta.id;
      if (meta.inArchive) itemsByName[key].inArchive = true;
      if (meta.mimeType) itemsByName[key].mimeType = meta.mimeType;
      if (meta.isPdf != null) itemsByName[key].isPdf = !!meta.isPdf;
    }
  }

  // フォルダ直下 = 未処理・OCR/反映待ち。元画像/ = シート追記済みの退避先。
  if (folderId) {
    try {
      listFolderFiles(folderId).forEach(function(f) {
        ensureItem(Object.assign({}, f, { inArchive: false, location: 'inbox' }));
      });
    } catch (e) { /* folder inaccessible */ }
  }

  try {
    var warpedFolder = getOrCreateTestImageFolder(ss);
    var warpedFiles = warpedFolder.getFiles();
    while (warpedFiles.hasNext()) {
      var wf = warpedFiles.next();
      var wfName = wf.getName();
      if (wfName.indexOf('補正_') !== 0) continue;
      var origName = parseWarpedOriginalFileName_(wfName);
      if (!origName || isFileNameInResults_(origName, processedNames)) continue;
      ensureItem({ id: '', name: origName, mimeType: 'image/jpeg', isPdf: false, inArchive: true });
      itemsByName[normalizeResultFileName_(origName)].stage = 'ocr_only';
      itemsByName[normalizeResultFileName_(origName)].warpedFileId = wf.getId();
    }
  } catch (e) { /* ignore */ }

  Object.keys(itemsByName).forEach(function(key) {
    var item = itemsByName[key];
    if (item.stage === 'ocr_only' && item.warpedFileId) return;
    if (item.id) {
      var wid = findWarpedFileInFolder_(item.id, item.name);
      if (wid) {
        item.stage = 'ocr_only';
        item.warpedFileId = wid;
      }
      return;
    }
    if (folderId) {
      var found = findSourceFileByName_(folderId, item.name);
      if (found) {
        item.id = found.id;
        item.inArchive = !!found.inArchive;
        item.mimeType = found.mimeType;
        item.isPdf = !!found.isPdf;
        var wid2 = findWarpedFileInFolder_(found.id, item.name);
        if (wid2) {
          item.stage = 'ocr_only';
          item.warpedFileId = wid2;
        }
      }
    }
  });

  var items = Object.keys(itemsByName).map(function(k) { return itemsByName[k]; });
  items.sort(function(a, b) { return naturalCompareFileNames_(a.name, b.name); });

  var ocrOnly = 0;
  var warpAndOcr = 0;
  var inInbox = 0;
  items.forEach(function(it) {
    if (it.stage === 'ocr_only') ocrOnly++;
    else warpAndOcr++;
    if (!it.inArchive) inInbox++;
  });

  return {
    items: items,
    stats: {
      pending: items.length,
      ocrOnly: ocrOnly,
      warpAndOcr: warpAndOcr,
      inInbox: inInbox,
      inSheet: Object.keys(processedNames).length
    }
  };
}

function getOcrWorkQueue() {
  return buildOcrWorkQueue_(getActiveTestSs());
}

function getProcessedSheetState() {
  var ss = getActiveTestSs();
  return {
    fileIds: Object.keys(getProcessedFileIds(ss)),
    fileNames: Object.keys(getProcessedFileNames(ss))
  };
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


// ========== OcrService.gs ==========

/**
 * Vision API OCR・生徒解答処理
 */

function callVisionAPI(imageBytes, languageHints) {
  var apiKey = PropertiesService.getScriptProperties().getProperty('VISION_API_KEY');
  if (!apiKey) throw new Error('VISION_API_KEY 未設定');
  var hints = languageHints && languageHints.length ? languageHints : ['ja'];
  var url = 'https://vision.googleapis.com/v1/images:annotate?key=' + apiKey;
  var payload = {
    requests: [{
      image: { content: imageBytes },
      features: [{ type: 'DOCUMENT_TEXT_DETECTION' }],
      imageContext: { languageHints: hints }
    }]
  };
  var response = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });
  var json = JSON.parse(response.getContentText());
  if (json.error) throw new Error('Vision API: ' + JSON.stringify(json.error));
  if (!json.responses || !json.responses[0]) throw new Error('Vision API 応答が空です');
  return json.responses[0];
}

function ocrLangToHints_(ocrLang) {
  return normalizeOcrLang_(ocrLang) === 'en' ? ['en'] : ['ja'];
}

function extractTextFromSingleCrop_(visionResult) {
  if (!visionResult || !visionResult.textAnnotations || !visionResult.textAnnotations.length) return 'なし';
  var t = String(visionResult.textAnnotations[0].description || '').trim();
  return t || 'なし';
}

function runOcrOnWarpedImage_(warpedBase64, fields, fieldCrops) {
  var imageBytes = warpedBase64.split(',')[1];
  var textMapping = {};
  if (fieldsNeedPerCropOcr_(fields) && fieldCrops && fieldCrops.length) {
    fieldCrops.forEach(function(fc) {
      if (!fc || !fc.fieldId || !fc.cropBase64) return;
      var cropBytes = String(fc.cropBase64).indexOf(',') >= 0
        ? fc.cropBase64.split(',')[1]
        : fc.cropBase64;
      var visionResult = callVisionAPI(cropBytes, ocrLangToHints_(fc.ocrLang));
      textMapping[String(fc.fieldId)] = extractTextFromSingleCrop_(visionResult);
    });
    fields.forEach(function(f) {
      if (textMapping[f.id] == null) textMapping[f.id] = 'なし';
    });
    return textMapping;
  }
  var unifiedLang = fields.length ? normalizeOcrLang_(fields[0].ocrLang) : 'en';
  var visionResult = callVisionAPI(imageBytes, ocrLangToHints_(unifiedLang));
  var extracted = extractTextFromBoxes(visionResult, fieldsToBoxes(fields));
  extracted.forEach(function(item) {
    textMapping[item.q_id] = item.student_answer;
  });
  fields.forEach(function(f) {
    if (textMapping[f.id] == null) textMapping[f.id] = 'なし';
  });
  return textMapping;
}

function extractTextFromBoxes(visionResult, targetBoxes) {
  if (!visionResult || !visionResult.textAnnotations) {
    return targetBoxes.map(function(box) {
      return { q_id: box.id, student_answer: 'なし' };
    });
  }
  var annotations = visionResult.textAnnotations;
  var result = [];
  targetBoxes.forEach(function(box) {
    var textInBox = [];
    for (var i = 1; i < annotations.length; i++) {
      var anno = annotations[i];
      var vertices = anno.boundingPoly ? anno.boundingPoly.vertices :
        (anno.boundingBox ? anno.boundingBox.vertices : anno.boundingVertice);
      if (!vertices || vertices.length < 4) continue;
      var cx = (vertices[0].x + vertices[1].x + vertices[2].x + vertices[3].x) / 4;
      var cy = (vertices[0].y + vertices[1].y + vertices[2].y + vertices[3].y) / 4;
      if (cx >= box.x && cx <= (box.x + box.w) && cy >= box.y && cy <= (box.y + box.h)) {
        textInBox.push({ text: anno.description, x: cx, y: cy });
      }
    }
    textInBox.sort(function(a, b) {
      if (Math.abs(a.y - b.y) > 15) return a.y - b.y;
      return a.x - b.x;
    });
    var finalString = textInBox.map(function(item) { return item.text; }).join('').trim();
    if (!finalString) finalString = 'なし';
    result.push({ q_id: box.id, student_answer: finalString });
  });
  return result;
}

function getProcessedFileIdList() {
  return Object.keys(getProcessedFileIds());
}

function buildResultRowArray(headers, map, fields, fileMeta, studentId, textMapping) {
  var row = new Array(headers.length).fill('');
  if (map.studentId >= 0) row[map.studentId] = studentId || '';
  if (map.fileName >= 0) row[map.fileName] = fileMeta.fileName || '';
  if (map.fileId >= 0) row[map.fileId] = fileMeta.fileId || '';
  if (map.warpedFileId >= 0) row[map.warpedFileId] = fileMeta.warpedFileId || '';
  fields.forEach(function(f) {
    var label = f.displayName || f.id;
    var fieldMap = map.fields[label];
    if (!fieldMap) return;
    if (fieldMap.text >= 0) row[fieldMap.text] = (textMapping && textMapping[f.id]) || 'なし';
  });
  return row;
}

function saveWarpedOnly(fileMeta, studentId, warpedBase64) {
  try {
    var ss = getActiveTestSs();
    var sourceFileId = fileMeta.id || fileMeta.fileId;
    var sourceFileName = fileMeta.name || fileMeta.fileName || '';
    if (!warpedBase64) throw new Error('補正画像データがありません。');
    var saved = saveWarpedImage(warpedBase64, sourceFileName, studentId, sourceFileId);
    return {
      success: true,
      warpedFileId: saved.fileId,
      reused: !!saved.reused,
      fileId: sourceFileId,
      fileName: sourceFileName
    };
  } catch (error) {
    return {
      success: false,
      error: error.toString(),
      fileId: fileMeta ? (fileMeta.id || fileMeta.fileId) : ''
    };
  }
}

function ocrStudentPaper(fileMeta, studentId, warpedBase64, fieldCrops, options) {
  try {
    var ss = getActiveTestSs();
    options = options || {};
    var sourceFileId = fileMeta.id || fileMeta.fileId;
    var sourceFileName = fileMeta.name || fileMeta.fileName || '';
    var fields = getAnswerFields(ss);
    if (fields.length === 0) throw new Error('記述欄が設定されていません。');

    var saved;
    if (options.skipSaveWarped) {
      var existingId = fileMeta.warpedFileId || options.warpedFileId || '';
      if (!existingId) existingId = getWarpedFileIdFromResults(ss, sourceFileId);
      if (!existingId) existingId = findWarpedFileInFolder_(sourceFileId, sourceFileName);
      if (!existingId) throw new Error('補正画像が見つかりません。');
      saved = { fileId: existingId, reused: true };
    } else {
      saved = saveWarpedImage(warpedBase64, sourceFileName, studentId, sourceFileId);
    }
    var ocrImage = warpedBase64;
    var hasPayload = ocrImage && String(ocrImage).indexOf('base64,') >= 0 && String(ocrImage).split(',')[1];
    if (!hasPayload) {
      if (fieldCrops && fieldCrops.length && fieldsNeedPerCropOcr_(fields)) {
        ocrImage = ocrImage || 'data:image/jpeg;base64,';
      } else {
        ocrImage = readWarpedDataUrlFromDrive_(saved.fileId);
      }
    }
    var textMapping = runOcrOnWarpedImage_(ocrImage, fields, fieldCrops || []);
    var cleanStudentId = (studentId && !String(studentId).includes('?')) ? String(studentId) : '';

    return {
      success: true,
      studentId: cleanStudentId,
      fileId: sourceFileId,
      fileName: sourceFileName,
      warpedFileId: saved.fileId,
      textMapping: textMapping,
      skipped: false,
      warpReused: !!saved.reused
    };
  } catch (error) {
    return {
      success: false,
      error: error.toString(),
      fileId: fileMeta ? (fileMeta.id || fileMeta.fileId) : ''
    };
  }
}

function padResultRow_(row, numCols) {
  var out = [];
  var n = numCols > 0 ? numCols : (row ? row.length : 0);
  for (var i = 0; i < n; i++) out.push(row && i < row.length ? row[i] : '');
  return out;
}

function escapeTsvCell_(value) {
  var s = String(value == null ? '' : value);
  if (s.indexOf('\t') >= 0 || s.indexOf('\n') >= 0 || s.indexOf('\r') >= 0 || s.indexOf('"') >= 0) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

function isPendingRowAlreadyInSheet_(ss, row) {
  if (!row) return false;
  var names = getProcessedFileNames(ss);
  var ids = getProcessedFileIds(ss);
  var n = normalizeResultFileName_(row.fileName);
  if (n && names[n]) return true;
  if (row.fileId && ids[String(row.fileId)]) return true;
  return false;
}

/** 採点結果シートを追記可能な状態にし、現在のヘッダー行を返す */
function prepareResultsSheetForAppend_(sheet, fields, ss) {
  ss = ss || getActiveTestSs();
  fields = fields || getAnswerFields(ss);
  var extra = getDynamicResultExtraColumns(ss);
  var expected = buildResultHeaders(fields, extra);
  if (sheet.getLastRow() === 0) {
    sheet.getRange(1, 1, 1, expected.length).setValues([expected]);
    sheet.setFrozenRows(1);
    return expected;
  }
  ensureWarpedFileIdColumn(sheet);
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  expected.forEach(function(h) {
    if (headers.indexOf(h) >= 0) return;
    var lastCol = sheet.getLastColumn();
    sheet.insertColumnAfter(lastCol);
    sheet.getRange(1, lastCol + 1).setValue(h);
    headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  });
  return headers;
}

function buildResultRowsTsv(rows) {
  if (!rows || rows.length === 0) return { tsv: '', headers: [], rowCount: 0 };
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  var fields = getAnswerFields(ss);
  var headers = prepareResultsSheetForAppend_(sheet, fields, ss);
  var map = getResultColumnMap(headers);
  var numCols = sheet.getLastColumn();
  var lines = [headers.map(escapeTsvCell_).join('\t')];
  rows.forEach(function(r) {
    var rowArr = padResultRow_(buildResultRowArray(headers, map, fields, {
      fileName: r.fileName,
      fileId: r.fileId,
      warpedFileId: r.warpedFileId
    }, r.studentId, r.textMapping), numCols);
    lines.push(rowArr.map(escapeTsvCell_).join('\t'));
  });
  return { tsv: lines.join('\n'), headers: headers, rowCount: rows.length };
}

function flushResultRows(rows) {
  if (!rows || rows.length === 0) return { written: 0, skipped: 0, errors: [], writtenFileNames: [], skippedFileNames: [] };

  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  var fields = getAnswerFields(ss);
  var headers = prepareResultsSheetForAppend_(sheet, fields, ss);
  var map = getResultColumnMap(headers);
  var numCols = sheet.getLastColumn();
  var written = 0;
  var skipped = 0;
  var errors = [];
  var writtenRows = [];
  var writtenFileNames = [];
  var skippedFileNames = [];
  var skippedRows = [];

  rows.forEach(function(r, idx) {
    try {
      if (isPendingRowAlreadyInSheet_(ss, r)) {
        skipped++;
        if (r.fileName) skippedFileNames.push(String(r.fileName));
        skippedRows.push(r);
        return;
      }
      var rowArr = padResultRow_(buildResultRowArray(headers, map, fields, {
        fileName: r.fileName,
        fileId: r.fileId,
        warpedFileId: r.warpedFileId
      }, r.studentId, r.textMapping), numCols);
      sheet.appendRow(rowArr);
      written++;
      writtenRows.push(r);
      if (r.fileName) writtenFileNames.push(String(r.fileName));
    } catch (e) {
      errors.push({
        index: idx,
        fileName: r.fileName || '',
        fileId: r.fileId || '',
        error: e.toString()
      });
    }
  });

  if (written > 0) {
    updateTestStatus('テキスト化中');
    touchTestProgress_(ss, 3);
  }
  var studentFolderId = getTestInfoValue(ss, '生徒解答フォルダID');
  var rowsToArchive = writtenRows.concat(skippedRows);
  var movedToArchive = moveResultRowsToOriginalArchive_(rowsToArchive, studentFolderId);
  return {
    written: written,
    skipped: skipped,
    errors: errors,
    movedToArchive: movedToArchive,
    writtenFileNames: writtenFileNames,
    skippedFileNames: skippedFileNames
  };
}

function processStudentPaper(fileMeta, studentId, warpedBase64, skipIfExists, fieldCrops) {
  try {
    var ss = getActiveTestSs();
    var sourceFileId = fileMeta.id || fileMeta.fileId;

    if (skipIfExists !== false && sourceFileId && isFileAlreadyProcessed(ss, sourceFileId)) {
      return { success: true, skipped: true, fileId: sourceFileId };
    }

    var ocrResult = ocrStudentPaper(fileMeta, studentId, warpedBase64, fieldCrops);
    if (!ocrResult.success) return ocrResult;

    appendResultRow(ss, {
      fileName: ocrResult.fileName,
      fileId: ocrResult.fileId,
      warpedFileId: ocrResult.warpedFileId
    }, ocrResult.studentId, ocrResult.textMapping);

    var folderId = getTestInfoValue(ss, '生徒解答フォルダID');
    moveSourceFileToOriginalArchive_(ocrResult.fileId, ocrResult.fileName, folderId);

    return {
      success: true,
      studentId: ocrResult.studentId,
      fileId: ocrResult.fileId,
      textMapping: ocrResult.textMapping,
      skipped: false
    };
  } catch (error) {
    return { success: false, error: error.toString(), fileId: fileMeta ? fileMeta.id : '' };
  }
}

function ensureWarpedFileIdColumn(sheet) {
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  if (headers.indexOf('補正画像FileID') >= 0) return headers;
  var fileIdIdx = headers.indexOf('ファイルID');
  if (fileIdIdx >= 0) {
    sheet.insertColumnAfter(fileIdIdx + 1);
    sheet.getRange(1, fileIdIdx + 2).setValue('補正画像FileID');
  } else {
    sheet.insertColumnAfter(3);
    sheet.getRange(1, 4).setValue('補正画像FileID');
  }
  return sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
}

function appendResultRow(ss, fileMeta, studentId, textMapping) {
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  var fields = getAnswerFields(ss);
  if (sheet.getLastRow() === 0) {
    initResultsSheet(sheet, fields, getDynamicResultExtraColumns(ss));
  }

  ensureWarpedFileIdColumn(sheet);
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var map = getResultColumnMap(headers);
  var row = buildResultRowArray(headers, map, fields, fileMeta, studentId, textMapping);
  sheet.appendRow(row);
}

function getResultRowCount() {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  return Math.max(0, sheet.getLastRow() - 1);
}


// ========== GradingService.gs ==========

/**
 * 採点基準・一括採点・考査総括
 */

function columnToLetter_(column) {
  var letter = '';
  while (column > 0) {
    var mod = (column - 1) % 26;
    letter = String.fromCharCode(65 + mod) + letter;
    column = Math.floor((column - mod - 1) / 26);
  }
  return letter;
}

function readResultColumnValues_(sheet, col1Based, startRow, endRow) {
  if (endRow < startRow) return [];
  var a1 = columnToLetter_(col1Based) + startRow + ':' + columnToLetter_(col1Based) + endRow;
  return sheet.getRange(a1).getValues();
}

function writeResultColumnValues_(sheet, col1Based, startRow, values) {
  if (!values || !values.length) return;
  var endRow = startRow + values.length - 1;
  var a1 = columnToLetter_(col1Based) + startRow + ':' + columnToLetter_(col1Based) + endRow;
  sheet.getRange(a1).setValues(values);
  SpreadsheetApp.flush();
}

function getFieldTextColName_(fieldId, fields) {
  var targetField = fields.find(function(f) { return f.id === fieldId; });
  if (!targetField) return null;
  var label = targetField.displayName || targetField.id;
  return label + '_テキスト';
}

function getResultSheetColumnIndices_(headers, textColName) {
  return {
    textCol: headers.indexOf(textColName),
    fileIdCol: headers.indexOf('ファイルID'),
    fileNameCol: headers.indexOf('ファイル名'),
    studentIdCol: headers.indexOf('生徒ID'),
    rowIndexCol: headers.indexOf('行番号')
  };
}

function getFieldAnswerDetails_(ss, fieldId) {
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  var lastRow = sheet.getLastRow();
  if (lastRow <= 1) return [];

  var fields = getAnswerFields(ss);
  var textColName = getFieldTextColName_(fieldId, fields);
  if (!textColName) return [];

  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var cols = getResultSheetColumnIndices_(headers, textColName);
  if (cols.textCol === -1) return [];

  var startRow = 2;
  var endRow = lastRow;
  var rowCount = endRow - startRow + 1;
  var texts = readResultColumnValues_(sheet, cols.textCol + 1, startRow, endRow);
  var fileIds = cols.fileIdCol >= 0 ? readResultColumnValues_(sheet, cols.fileIdCol + 1, startRow, endRow) : [];
  var fileNames = cols.fileNameCol >= 0 ? readResultColumnValues_(sheet, cols.fileNameCol + 1, startRow, endRow) : [];
  var studentIds = cols.studentIdCol >= 0 ? readResultColumnValues_(sheet, cols.studentIdCol + 1, startRow, endRow) : [];

  var details = [];
  for (var i = 0; i < rowCount; i++) {
    var answer = String(texts[i][0]).trim();
    if (!answer) answer = 'なし';
    details.push({
      rowIndex: i + 2,
      answer: answer,
      fileId: fileIds.length ? String(fileIds[i][0] || '') : '',
      fileName: fileNames.length ? String(fileNames[i][0] || '') : '',
      studentId: studentIds.length ? String(studentIds[i][0] || '') : ''
    });
  }
  return details;
}

function applyReplacementRules_(text, rules) {
  var result = String(text);
  (rules || []).forEach(function(r) {
    if (!r || !r.search) return;
    if (r.useRegex) {
      try {
        result = result.replace(new RegExp(r.search, 'gi'), r.replace || '');
      } catch (e) { /* invalid regex */ }
    } else {
      var parts = result.split(r.search);
      result = parts.join(r.replace || '');
    }
  });
  result = result.trim();
  return result || 'なし';
}

function getOcrReplacementsForSs(ss, fieldId) {
  ensureOcrReplacementsSheet(ss);
  var sheet = ss.getSheetByName(SHEET_OCR_REPLACEMENTS);
  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return [];

  var rules = [];
  for (var i = 1; i < data.length; i++) {
    var fid = String(data[i][0] || '').trim();
    if (!fid) continue;
    if (fieldId && fid !== fieldId) continue;
    var search = String(data[i][1] || '');
    if (!search) continue;
    rules.push({
      fieldId: fid,
      search: search,
      replace: String(data[i][2] != null ? data[i][2] : ''),
      useRegex: data[i][3] === true || String(data[i][3]).toUpperCase() === 'TRUE'
    });
  }
  return rules;
}

function getOcrReplacements(fieldId) {
  return getOcrReplacementsForSs(getActiveTestSs(), fieldId);
}

function saveOcrReplacements(fieldId, rules) {
  var ss = getActiveTestSs();
  ensureOcrReplacementsSheet(ss);
  var sheet = ss.getSheetByName(SHEET_OCR_REPLACEMENTS);
  var data = sheet.getDataRange().getValues();
  var kept = data.length > 1 ? [data[0]] : [['記述欄ID', '検索文字列', '置換後', '正規表現']];

  for (var i = 1; i < data.length; i++) {
    if (String(data[i][0] || '').trim() !== fieldId) kept.push(data[i]);
  }
  (rules || []).forEach(function(r) {
    if (!r || !r.search) return;
    kept.push([
      fieldId,
      r.search,
      r.replace != null ? r.replace : '',
      r.useRegex ? true : false
    ]);
  });

  sheet.clearContents();
  if (kept.length) sheet.getRange(1, 1, kept.length, kept[0].length).setValues(kept);
  sheet.setFrozenRows(1);
  return getOcrReplacements(fieldId);
}

function rewriteFieldTextColumn_(ss, fieldId, shouldRewriteFn, newText) {
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  var lastRow = sheet.getLastRow();
  if (lastRow <= 1) return 0;

  var fields = getAnswerFields(ss);
  var textColName = getFieldTextColName_(fieldId, fields);
  if (!textColName) return 0;

  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var textCol = headers.indexOf(textColName);
  if (textCol === -1) return 0;

  var startRow = 2;
  var endRow = lastRow;
  var texts = readResultColumnValues_(sheet, textCol + 1, startRow, endRow);
  var updatedCount = 0;
  var canonical = String(newText || '').trim() || 'なし';

  for (var i = 0; i < texts.length; i++) {
    var oldVal = String(texts[i][0]).trim() || 'なし';
    if (shouldRewriteFn(oldVal) && oldVal !== canonical) {
      texts[i][0] = canonical;
      updatedCount++;
    }
  }
  if (updatedCount > 0) {
    writeResultColumnValues_(sheet, textCol + 1, startRow, texts);
  }
  return updatedCount;
}

function applyTextReplacementsToField(fieldId, rules) {
  var ss = getActiveTestSs();
  if (rules && rules.length) saveOcrReplacements(fieldId, rules);
  else rules = getOcrReplacements(fieldId);

  if (!rules.length) {
    return { answers: getUniqueAnswers(fieldId), replacedCount: 0 };
  }

  var sheet = ss.getSheetByName(SHEET_RESULTS);
  var lastRow = sheet.getLastRow();
  if (lastRow <= 1) return { answers: [], replacedCount: 0 };

  var fields = getAnswerFields(ss);
  var textColName = getFieldTextColName_(fieldId, fields);
  if (!textColName) return { answers: [], replacedCount: 0 };

  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var textCol = headers.indexOf(textColName);
  if (textCol === -1) throw new Error('採点結果シートに列「' + textColName + '」が見つかりません。③テキスト化を実行してください。');

  var startRow = 2;
  var endRow = lastRow;
  var texts = readResultColumnValues_(sheet, textCol + 1, startRow, endRow);
  var replacedCount = 0;
  for (var i = 0; i < texts.length; i++) {
    var oldVal = String(texts[i][0]).trim() || 'なし';
    var newVal = applyReplacementRules_(texts[i][0], rules);
    if (oldVal !== newVal) replacedCount++;
    texts[i][0] = newVal;
  }
  writeResultColumnValues_(sheet, textCol + 1, startRow, texts);
  return { answers: getUniqueAnswers(fieldId), replacedCount: replacedCount, textColName: textColName };
}

function getOcrResultPreview() {
  return getOcrResultPreview_(getActiveTestSs());
}

function getUniqueAnswers(fieldId) {
  var ss = getActiveTestSs();
  var details = getFieldAnswerDetails_(ss, fieldId);
  if (!details.length) return [];

  var countMap = {};
  details.forEach(function(row) {
    countMap[row.answer] = (countMap[row.answer] || 0) + 1;
  });

  return Object.keys(countMap).map(function(key) {
    return { answer_text: key, count: countMap[key] };
  }).sort(function(a, b) { return b.count - a.count; });
}

function getOutlierAnswerGroups(fieldId, maxCount) {
  maxCount = maxCount != null ? parseInt(maxCount, 10) : 1;
  if (isNaN(maxCount) || maxCount < 1) maxCount = 1;

  var ss = getActiveTestSs();
  var details = getFieldAnswerDetails_(ss, fieldId);
  var countMap = {};
  details.forEach(function(row) {
    if (!countMap[row.answer]) {
      countMap[row.answer] = { answer_text: row.answer, count: 0, rows: [] };
    }
    countMap[row.answer].count++;
    countMap[row.answer].rows.push({
      rowIndex: row.rowIndex,
      studentId: row.studentId,
      fileName: row.fileName,
      fileId: row.fileId
    });
  });

  return Object.keys(countMap)
    .filter(function(k) { return countMap[k].count <= maxCount; })
    .map(function(k) { return countMap[k]; })
    .sort(function(a, b) { return a.count - b.count || a.answer_text.localeCompare(b.answer_text); });
}

function getAnswerRowsForPattern(fieldId, answerText) {
  var ss = getActiveTestSs();
  var details = getFieldAnswerDetails_(ss, fieldId);
  var target = String(answerText || '').trim() || 'なし';
  return details.filter(function(row) { return row.answer === target; }).map(function(row) {
    return {
      rowIndex: row.rowIndex,
      studentId: row.studentId,
      fileName: row.fileName,
      fileId: row.fileId,
      answer_text: row.answer
    };
  });
}

function getDeemedDraftForSs_(ss, fieldId) {
  ensureDeemedDraftSheet(ss);
  var sheet = ss.getSheetByName(SHEET_DEEMED_DRAFT);
  var data = sheet.getDataRange().getValues();
  var canonical = '';
  var sources = [];
  for (var i = 1; i < data.length; i++) {
    if (String(data[i][0] || '').trim() !== fieldId) continue;
    canonical = String(data[i][1] || '');
    var src = String(data[i][2] || '').trim();
    if (src) sources.push(src);
  }
  return { canonical: canonical, sources: sources };
}

function getDeemedDraftGrouped_(ss) {
  ensureDeemedDraftSheet(ss);
  var sheet = ss.getSheetByName(SHEET_DEEMED_DRAFT);
  var data = sheet.getDataRange().getValues();
  var grouped = {};
  for (var i = 1; i < data.length; i++) {
    var fid = String(data[i][0] || '').trim();
    if (!fid) continue;
    if (!grouped[fid]) grouped[fid] = { canonical: '', sources: [] };
    grouped[fid].canonical = String(data[i][1] || '');
    var src = String(data[i][2] || '').trim();
    if (src && grouped[fid].sources.indexOf(src) < 0) grouped[fid].sources.push(src);
  }
  return grouped;
}

function getDeemedScoringGrouped_(ss) {
  ensureDeemedScoringSheet(ss);
  var sheet = ss.getSheetByName(SHEET_DEEMED_SCORING);
  var data = sheet.getDataRange().getValues();
  var grouped = {};
  for (var i = 1; i < data.length; i++) {
    var fid = String(data[i][0] || '').trim();
    if (!fid) continue;
    if (!grouped[fid]) grouped[fid] = { canonical: '', sources: [] };
    grouped[fid].canonical = String(data[i][1] || '');
    var src = String(data[i][2] || '').trim();
    if (src && grouped[fid].sources.indexOf(src) < 0) grouped[fid].sources.push(src);
  }
  return grouped;
}

function getDeemedScoring(fieldId) {
  return getDeemedDraftForSs_(getActiveTestSs(), fieldId);
}

function saveDeemedScoringDraft(fieldId, canonical, sources) {
  var ss = getActiveTestSs();
  ensureDeemedDraftSheet(ss);
  var sheet = ss.getSheetByName(SHEET_DEEMED_DRAFT);
  var data = sheet.getDataRange().getValues();
  var kept = data.length > 1 ? [data[0]] : [['記述欄ID', '正答例', '元解答']];

  for (var i = 1; i < data.length; i++) {
    if (String(data[i][0] || '').trim() !== fieldId) kept.push(data[i]);
  }
  canonical = String(canonical || '').trim();
  (sources || []).forEach(function(src) {
    src = String(src || '').trim();
    if (!src) return;
    kept.push([fieldId, canonical, src]);
  });

  sheet.clearContents();
  if (kept.length) sheet.getRange(1, 1, kept.length, kept[0].length).setValues(kept);
  sheet.setFrozenRows(1);
  return getDeemedDraftForSs_(ss, fieldId);
}

function applyDeemedScoringToField(fieldId, canonical, sources) {
  var ss = getActiveTestSs();
  canonical = String(canonical || '').trim();
  if (!canonical) throw new Error('正答例を入力してください。');

  var sourceSet = {};
  (sources || []).forEach(function(s) {
    s = String(s || '').trim();
    if (s && s !== canonical) sourceSet[s] = true;
  });
  var sourceList = Object.keys(sourceSet);
  if (!sourceList.length) throw new Error('みなし対象の解答を1件以上選択してください。');

  saveDeemedScoringDraft(fieldId, canonical, sourceList);

  var updatedCount = rewriteFieldTextColumn_(ss, fieldId, function(answer) {
    return !!sourceSet[answer];
  }, canonical);

  ensureDeemedScoringSheet(ss);
  var auditSheet = ss.getSheetByName(SHEET_DEEMED_SCORING);
  var now = Utilities.formatDate(new Date(), 'JST', 'yyyy-MM-dd HH:mm:ss');
  sourceList.forEach(function(src) {
    auditSheet.appendRow([fieldId, canonical, src, now]);
  });

  var draftSheet = ss.getSheetByName(SHEET_DEEMED_DRAFT);
  var draftData = draftSheet.getDataRange().getValues();
  var keptDraft = draftData.length > 1 ? [draftData[0]] : [['記述欄ID', '正答例', '元解答']];
  for (var i = 1; i < draftData.length; i++) {
    if (String(draftData[i][0] || '').trim() !== fieldId) keptDraft.push(draftData[i]);
  }
  draftSheet.clearContents();
  if (keptDraft.length) draftSheet.getRange(1, 1, keptDraft.length, keptDraft[0].length).setValues(keptDraft);
  draftSheet.setFrozenRows(1);

  return { answers: getUniqueAnswers(fieldId), updatedCount: updatedCount, canonical: canonical };
}

function generateRubricWithGemini(fieldId, uniqueAnswersArray) {
  var ss = getActiveTestSs();
  var pointsMap = getPointsMap(ss);
  var maxScore = pointsMap[fieldId] || 5;

  var apiKey = PropertiesService.getScriptProperties().getProperty('GEMINI_API_KEY');
  if (!apiKey) throw new Error('GEMINI_API_KEY 未設定');

  var url = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=' + apiKey;
  var prompt = {
    system_instruction: {
      parts: [{ text: 'あなたは厳格かつ公平なテスト採点基準を策定する専門家です。各解答に対し、○（満点）、△（部分点）、×（0点）の判定と付与得点（0〜満点の整数）および根拠をJSONで返してください。解答が「なし」の場合は×・0点としてください。' }]
    },
    contents: [{
      parts: [{ text: '記述欄ID: ' + fieldId + ', 満点: ' + maxScore + '点。ユニーク解答リスト:\n' + JSON.stringify(uniqueAnswersArray) }]
    }],
    generationConfig: {
      responseMimeType: 'application/json',
      responseSchema: {
        type: 'OBJECT',
        properties: {
          scrutinized_list: {
            type: 'ARRAY',
            items: {
              type: 'OBJECT',
              properties: {
                answer_text: { type: 'STRING' },
                judgment: { type: 'STRING' },
                recommended_score: { type: 'INTEGER' },
                reason: { type: 'STRING' }
              },
              required: ['answer_text', 'judgment', 'recommended_score', 'reason']
            }
          }
        },
        required: ['scrutinized_list']
      }
    }
  };

  var response = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(prompt),
    muteHttpExceptions: true
  });
  var body = JSON.parse(response.getContentText());
  if (body.error) throw new Error('Gemini API: ' + JSON.stringify(body.error));
  return JSON.parse(body.candidates[0].content.parts[0].text);
}

function saveGradingCriteria(fieldId, confirmedRules, testSsId) {
  var ss = testSsId ? SpreadsheetApp.openById(testSsId) : getActiveTestSs();
  if (testSsId) {
    PropertiesService.getScriptProperties().setProperty('ACTIVE_TEST_SS_ID', testSsId);
  }
  var sheet = ensureCriteriaSheet(ss);
  var data = sheet.getDataRange().getValues();
  var kept = [];

  for (var i = 1; i < data.length; i++) {
    if (String(data[i][0]) !== String(fieldId)) {
      kept.push(data[i]);
    }
  }

  (confirmedRules || []).forEach(function(rule) {
    kept.push([
      fieldId,
      rule.answer_text,
      rule.judgment || '×',
      parseInt(rule.score, 10) || 0,
      rule.reason || ''
    ]);
  });

  sheet.clear();
  sheet.appendRow(['記述欄ID', '解答パターン', '判定', '付与得点', '備考']);
  if (kept.length) {
    sheet.getRange(2, 1, kept.length, 5).setValues(kept);
  }
  SpreadsheetApp.flush();
  touchTestProgress_(ss, 4);
  return getCriteriaGroupedByField_(ss)[String(fieldId)] || [];
}

function getGradingCriteria(ss) {
  ss = ss || getActiveTestSs();
  var sheet = ensureCriteriaSheet(ss);
  var data = sheet.getDataRange().getValues();
  var rules = [];
  for (var i = 1; i < data.length; i++) {
    rules.push({
      fieldId: String(data[i][0]),
      answer_text: String(data[i][1]),
      judgment: String(data[i][2]),
      score: parseInt(data[i][3], 10) || 0,
      reason: String(data[i][4] || '')
    });
  }
  return rules;
}

function buildRuleMap(ss) {
  var rules = getGradingCriteria(ss);
  var ruleMap = {};
  rules.forEach(function(r) {
    if (!ruleMap[r.fieldId]) ruleMap[r.fieldId] = {};
    ruleMap[r.fieldId][String(r.answer_text).trim()] = {
      judgment: r.judgment,
      score: r.score
    };
  });
  return ruleMap;
}

function executeGrading() {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (sheet.getLastRow() <= 1) throw new Error('採点対象データがありません。');

  var fields = getAnswerFields(ss);
  var ruleMap = buildRuleMap(ss);
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);
  var data = sheet.getRange(2, 1, sheet.getLastRow() - 1, sheet.getLastColumn()).getValues();
  var unregisteredCount = 0;

  for (var r = 0; r < data.length; r++) {
    var row = data[r];
    fields.forEach(function(f) {
      var label = f.displayName || f.id;
      var fm = colMap.fields[label];
      if (!fm) return;
      var answer = String(row[fm.text] || '').trim() || 'なし';
      var rule = ruleMap[f.id] && ruleMap[f.id][answer];
      if (rule) {
        row[fm.judgment] = rule.judgment;
        row[fm.score] = rule.score;
      } else {
        row[fm.judgment] = '×';
        row[fm.score] = 0;
        unregisteredCount++;
      }
    });
    data[r] = row;
  }

  sheet.getRange(2, 1, data.length, headers.length).setValues(data);
  calculateDomainScores();
  applyExternalScoresToResults();
  buildSummary(ss, unregisteredCount);
  updateTestStatus('採点完了');
  touchTestProgress_(ss, 5);
  return { gradedCount: data.length, unregisteredCount: unregisteredCount };
}

function buildSummary(ss, unregisteredCount) {
  ss = ss || getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_SUMMARY);
  sheet.clear();
  sheet.appendRow(['区分', '項目', '値', '備考']);

  var resultSheet = ss.getSheetByName(SHEET_RESULTS);
  var headers = resultSheet.getRange(1, 1, 1, resultSheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);
  var data = resultSheet.getLastRow() > 1
    ? resultSheet.getRange(2, 1, resultSheet.getLastRow() - 1, resultSheet.getLastColumn()).getValues()
    : [];
  var fields = getAnswerFields(ss);
  var studentCount = data.length;

  sheet.appendRow(['全体', '受験者数', studentCount, '']);
  sheet.appendRow(['全体', '未登録パターン照合数', unregisteredCount || 0, '採点基準に無い解答']);

  fields.forEach(function(f) {
    var label = f.displayName || f.id;
    var fm = colMap.fields[label];
    if (!fm) return;
    var counts = { '○': 0, '△': 0, '×': 0, other: 0 };
    var totalScore = 0;
    data.forEach(function(row) {
      var j = String(row[fm.judgment] || '');
      if (counts[j] !== undefined) counts[j]++;
      else counts.other++;
      totalScore += parseInt(row[fm.score], 10) || 0;
    });
    var denom = studentCount || 1;
    sheet.appendRow(['設問', label + '_○人数', counts['○'], '']);
    sheet.appendRow(['設問', label + '_△人数', counts['△'], '']);
    sheet.appendRow(['設問', label + '_×人数', counts['×'], '']);
    sheet.appendRow(['設問', label + '_○率', Math.round(counts['○'] / denom * 1000) / 10 + '%', '']);
    sheet.appendRow(['設問', label + '_△率', Math.round(counts['△'] / denom * 1000) / 10 + '%', '']);
    sheet.appendRow(['設問', label + '_×率', Math.round(counts['×'] / denom * 1000) / 10 + '%', '']);
    sheet.appendRow(['設問', label + '_平均点', studentCount ? Math.round(totalScore / studentCount * 100) / 100 : 0, '']);
  });

  var domainLabels = getDomainColumnLabels(ss);
  domainLabels.forEach(function(dl) {
    var idx = headers.indexOf(dl);
    if (idx < 0) return;
    var maxPossible = getDomainMaxScore(ss, dl);
    var total = 0;
    data.forEach(function(row) { total += parseFloat(row[idx]) || 0; });
    var rate = studentCount && maxPossible ? Math.round(total / (studentCount * maxPossible) * 1000) / 10 : 0;
    sheet.appendRow(['領域', dl + '_平均', studentCount ? Math.round(total / studentCount * 100) / 100 : 0, '']);
    sheet.appendRow(['領域', dl + '_得点率', rate + '%', '満点合計=' + maxPossible]);
  });

  return sheet.getLastRow() - 1;
}

function getDomainMaxScore(ss, domainLabel) {
  var domains = getDomainSettings(ss);
  var fields = getAnswerFields(ss);
  var points = getPointsMap(ss);
  var match = domainLabel.match(/^(大問|範囲|能力)(.+)_得点$/);
  if (!match) return 0;
  var typeMap = { '大問': 'daiMon', '範囲': 'hanI', '能力': 'noryoku' };
  var key = match[2];
  var type = typeMap[match[1]];
  var total = 0;
  domains.forEach(function(d) {
    var fieldPoints = points[d.fieldId] || 0;
    if (type === 'daiMon' && String(d.daiMon) === key) total += fieldPoints;
    if (type === 'hanI' && String(d.hanI) === key) total += fieldPoints;
    if (type === 'noryoku' && String(d.noryoku) === key) total += fieldPoints;
  });
  return total;
}

function getSummaryData() {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_SUMMARY);
  if (sheet.getLastRow() <= 1) return [];
  return sheet.getRange(2, 1, sheet.getLastRow() - 1, 4).getValues().map(function(row) {
    return { category: row[0], item: row[1], value: row[2], note: row[3] };
  });
}


// ========== DomainService.gs ==========

/**
 * 領域設定・領域別得点集計
 */

function getDomainSettings(ss) {
  ss = ss || getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_DOMAINS);
  var data = sheet.getDataRange().getValues();
  var list = [];
  for (var i = 1; i < data.length; i++) {
    if (!data[i][0]) continue;
    list.push({
      fieldId: String(data[i][0]),
      daiMon: data[i][1] != null ? String(data[i][1]) : '',
      hanI: data[i][2] != null ? String(data[i][2]) : '',
      noryoku: data[i][3] != null ? String(data[i][3]) : ''
    });
  }
  return list;
}

function saveDomainSettings(settings) {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_DOMAINS);
  sheet.clear();
  sheet.appendRow(['記述欄ID', '大問', '範囲', '能力']);
  settings.forEach(function(s) {
    sheet.appendRow([s.fieldId, s.daiMon || '', s.hanI || '', s.noryoku || '']);
  });
  rebuildResultsSheetHeaders(ss);
  touchTestProgress_(ss, 6);
  return getDomainSettings(ss);
}

function calculateDomainScores() {
  var ss = getActiveTestSs();
  var domains = getDomainSettings(ss);
  var fields = getAnswerFields(ss);
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (sheet.getLastRow() <= 1) return;

  rebuildResultsSheetHeaders(ss);
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);
  var data = sheet.getRange(2, 1, sheet.getLastRow() - 1, sheet.getLastColumn()).getValues();

  var daiMonGroups = {}, hanIGroups = {}, noryokuGroups = {};
  domains.forEach(function(d) {
    if (d.daiMon) {
      if (!daiMonGroups[d.daiMon]) daiMonGroups[d.daiMon] = [];
      daiMonGroups[d.daiMon].push(d.fieldId);
    }
    if (d.hanI) {
      if (!hanIGroups[d.hanI]) hanIGroups[d.hanI] = [];
      hanIGroups[d.hanI].push(d.fieldId);
    }
    if (d.noryoku) {
      if (!noryokuGroups[d.noryoku]) noryokuGroups[d.noryoku] = [];
      noryokuGroups[d.noryoku].push(d.fieldId);
    }
  });

  function sumFieldScores(row, fieldIds) {
    var total = 0;
    fieldIds.forEach(function(fid) {
      var f = fields.find(function(x) { return x.id === fid; });
      if (!f) return;
      var label = f.displayName || f.id;
      var fm = colMap.fields[label];
      if (fm && fm.score >= 0) total += parseInt(row[fm.score], 10) || 0;
    });
    return total;
  }

  for (var r = 0; r < data.length; r++) {
    var row = data[r];

    Object.keys(daiMonGroups).forEach(function(k) {
      var colName = '大問' + k + '_得点';
      var idx = headers.indexOf(colName);
      if (idx >= 0) row[idx] = sumFieldScores(row, daiMonGroups[k]);
    });
    Object.keys(hanIGroups).forEach(function(k) {
      var colName = '範囲' + k + '_得点';
      var idx = headers.indexOf(colName);
      if (idx >= 0) row[idx] = sumFieldScores(row, hanIGroups[k]);
    });
    Object.keys(noryokuGroups).forEach(function(k) {
      var colName = '能力' + k + '_得点';
      var idx = headers.indexOf(colName);
      if (idx >= 0) row[idx] = sumFieldScores(row, noryokuGroups[k]);
    });

    // 総計点は記述欄ごとの得点合計＋外部得点（領域列は内訳表示のみで加算しない）
    var subtotal = 0;
    fields.forEach(function(f) {
      var label = f.displayName || f.id;
      var fm = colMap.fields[label];
      if (fm && fm.score >= 0) subtotal += parseInt(row[fm.score], 10) || 0;
    });

    var extIdx = headers.indexOf('外部連携得点');
    var extScore = extIdx >= 0 ? (parseFloat(row[extIdx]) || 0) : 0;
    var totalIdx = headers.indexOf('総計点');
    if (totalIdx >= 0) row[totalIdx] = subtotal + extScore;

    data[r] = row;
  }

  sheet.getRange(2, 1, data.length, headers.length).setValues(data);
  return data.length;
}

function getDomainSettingsForUi() {
  var ss = getActiveTestSs();
  var fields = getAnswerFields(ss);
  var domains = getDomainSettings(ss);
  var domainMap = {};
  domains.forEach(function(d) { domainMap[d.fieldId] = d; });

  return fields.map(function(f) {
    var d = domainMap[f.id] || {};
    return {
      fieldId: f.id,
      displayName: f.displayName || f.id,
      daiMon: d.daiMon || '',
      hanI: d.hanI || '',
      noryoku: d.noryoku || ''
    };
  });
}


// ========== ExternalScoreService.gs ==========

/**
 * 外部連携得点（マークシートリーダー等）のインポート
 */

function importExternalScores(rows) {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_EXTERNAL_SCORES);
  var now = Utilities.formatDate(new Date(), 'JST', 'yyyy-MM-dd HH:mm:ss');

  rows.forEach(function(r) {
    if (!r.studentId) return;
    sheet.appendRow([
      String(r.studentId),
      parseFloat(r.score) || 0,
      r.source || 'CSV取込',
      now
    ]);
  });

  applyExternalScoresToResults();
  touchTestProgress_(getActiveTestSs(), 7);
  return sheet.getLastRow() - 1;
}

function applyExternalScoresToResults() {
  var ss = getActiveTestSs();
  var extSheet = ss.getSheetByName(SHEET_EXTERNAL_SCORES);
  var resultSheet = ss.getSheetByName(SHEET_RESULTS);
  if (extSheet.getLastRow() <= 1 || resultSheet.getLastRow() <= 1) return 0;

  var extData = extSheet.getDataRange().getValues();
  var scoreByStudent = {};
  for (var i = 1; i < extData.length; i++) {
    var sid = String(extData[i][0]).trim();
    if (sid) scoreByStudent[sid] = parseFloat(extData[i][1]) || 0;
  }

  var headers = resultSheet.getRange(1, 1, 1, resultSheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);
  var extCol = headers.indexOf('外部連携得点');
  var totalCol = headers.indexOf('総計点');
  if (extCol < 0) return 0;

  var data = resultSheet.getRange(2, 1, resultSheet.getLastRow() - 1, resultSheet.getLastColumn()).getValues();
  var fields = getAnswerFields(ss);
  var applied = 0;

  for (var r = 0; r < data.length; r++) {
    var sid = String(data[r][colMap.studentId] || '').trim();
    if (sid && scoreByStudent[sid] !== undefined) {
      data[r][extCol] = scoreByStudent[sid];
      applied++;

      if (totalCol >= 0) {
        var subtotal = 0;
        fields.forEach(function(f) {
          var label = f.displayName || f.id;
          var fm = colMap.fields[label];
          if (fm && fm.score >= 0) subtotal += parseInt(data[r][fm.score], 10) || 0;
        });
        data[r][totalCol] = subtotal + scoreByStudent[sid];
      }
    }
  }

  resultSheet.getRange(2, 1, data.length, headers.length).setValues(data);
  return applied;
}

function getExternalScores() {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_EXTERNAL_SCORES);
  if (sheet.getLastRow() <= 1) return [];
  var data = sheet.getDataRange().getValues();
  var list = [];
  for (var i = 1; i < data.length; i++) {
    list.push({
      studentId: String(data[i][0]),
      score: parseFloat(data[i][1]) || 0,
      source: String(data[i][2] || ''),
      importedAt: data[i][3]
    });
  }
  return list;
}

function parseExternalScoresCsv(csvText) {
  var lines = String(csvText || '').split(/\r?\n/).filter(function(l) { return l.trim(); });
  var rows = [];
  lines.forEach(function(line, idx) {
    var parts = line.split(/[,;\t]/);
    if (parts.length < 2) return;
    if (idx === 0 && (parts[0].indexOf('ID') >= 0 || parts[0].indexOf('id') >= 0 || parts[0].indexOf('生徒') >= 0)) return;
    rows.push({
      studentId: parts[0].trim(),
      score: parseFloat(parts[1]) || 0,
      source: parts[2] ? parts[2].trim() : 'CSV取込'
    });
  });
  return rows;
}

function importExternalScoresFromCsv(csvText) {
  var rows = parseExternalScoresCsv(csvText);
  if (rows.length === 0) throw new Error('有効なCSVデータがありません。');
  return importExternalScores(rows);
}


// ========== RosterService.gs ==========

function initHubRosterSheet_(ss) {
  ensureHubRosterSheet_(ss);
}

function ensureHubRosterSheet_(ss) {
  var sheet = ss.getSheetByName(SHEET_ROSTER);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_ROSTER);
    writeHubRosterTemplate_(sheet);
    return sheet;
  }
  if (sheet.getLastRow() === 0) {
    writeHubRosterTemplate_(sheet);
    return sheet;
  }
  ensureRosterHeaders_(sheet);
  return sheet;
}

function ensureRosterHeaders_(sheet) {
  var headers = sheet.getRange(1, 1, 1, Math.max(sheet.getLastColumn(), ROSTER_HEADERS.length)).getValues()[0];
  var needsHeader = false;
  if (!headers || !headers[0] || String(headers[0]).trim() === '') {
    needsHeader = true;
  } else {
    for (var i = 0; i < ROSTER_HEADERS.length; i++) {
      if (String(headers[i] || '').trim() !== ROSTER_HEADERS[i]) {
        needsHeader = true;
        break;
      }
    }
  }
  if (needsHeader) {
    var headerRow = [ROSTER_HEADERS.slice()];
    sheet.getRange(1, 1, 1, ROSTER_HEADERS.length).setValues(headerRow);
    sheet.setFrozenRows(1);
    formatHubRosterSheet_(sheet);
  }
}

function writeHubRosterTemplate_(sheet) {
  sheet.clear();
  var sampleRow = ['（記入例・削除可）', '1001', '2', '1', '15', '山田太郎', '', '', ''];
  while (sampleRow.length < ROSTER_HEADERS.length) sampleRow.push('');
  var templateRows = [ROSTER_HEADERS.slice(), sampleRow];
  sheet.getRange(1, 1, templateRows.length, ROSTER_HEADERS.length).setValues(templateRows);
  sheet.setFrozenRows(1);
  formatHubRosterSheet_(sheet);
}

function formatHubRosterSheet_(sheet) {
  sheet.getRange(1, 1, 1, ROSTER_HEADERS.length).setFontWeight('bold').setBackground('#f3f4f6');
  for (var c = 1; c <= ROSTER_HEADERS.length; c++) {
    sheet.setColumnWidth(c, c === 1 ? 120 : (c === 6 ? 140 : 100));
  }
}

/** Web UI から Hub 名簿シートの存在を保証（ひな形自動作成） */
function ensureHubRosterSheet() {
  var ss = getHubSs();
  ensureHubRosterSheet_(ss);
  return { ok: true, sheetName: SHEET_ROSTER };
}

function isRosterTemplateRow_(rosterName) {
  var n = String(rosterName || '').trim();
  return !n || n.indexOf('（記入例') === 0 || n.indexOf('(記入例') === 0;
}

function getRosterSheet_() {
  var ss = getHubSs();
  return ensureHubRosterSheet_(ss);
}

function rosterRowFromSheet_(row) {
  return {
    rosterName: String(row[0] || ''),
    studentId: String(row[1] || ''),
    year: String(row[2] || ''),
    classNo: String(row[3] || ''),
    number: String(row[4] || ''),
    name: String(row[5] || ''),
    attr1: String(row[6] || ''),
    attr2: String(row[7] || ''),
    attr3: String(row[8] || '')
  };
}

function listRosterNames() {
  var sheet = getRosterSheet_();
  if (sheet.getLastRow() <= 1) return [];
  var data = sheet.getRange(2, 1, sheet.getLastRow(), 1).getValues();
  var names = {};
  data.forEach(function(r) {
    var n = String(r[0] || '').trim();
    if (n && !isRosterTemplateRow_(n)) names[n] = true;
  });
  return Object.keys(names).sort(function(a, b) { return a.localeCompare(b, 'ja'); });
}

function getRosterRows(rosterName) {
  if (!rosterName) return [];
  var sheet = getRosterSheet_();
  if (sheet.getLastRow() <= 1) return [];
  var data = sheet.getRange(2, 1, sheet.getLastRow(), ROSTER_HEADERS.length).getValues();
  var rows = [];
  data.forEach(function(r) {
    if (String(r[0] || '').trim() !== String(rosterName).trim()) return;
    var row = rosterRowFromSheet_(r);
    row.rowIndex = rows.length;
    rows.push(row);
  });
  return rows;
}

function saveRosterRows(rosterName, rows) {
  if (!rosterName || !String(rosterName).trim()) throw new Error('名簿名を指定してください。');
  rosterName = String(rosterName).trim();
  var sheet = getRosterSheet_();
  var lastRow = sheet.getLastRow();
  var kept = [];
  if (lastRow > 1) {
    var data = sheet.getRange(2, 1, lastRow, ROSTER_HEADERS.length).getValues();
    data.forEach(function(r) {
      if (String(r[0] || '').trim() !== rosterName) kept.push(r);
    });
    sheet.getRange(2, 1, lastRow, ROSTER_HEADERS.length).clearContent();
  }
  var out = kept.slice();
  (rows || []).forEach(function(r) {
    out.push([
      rosterName,
      r.studentId || '',
      r.year || '',
      r.classNo || '',
      r.number || '',
      r.name || '',
      r.attr1 || '',
      r.attr2 || '',
      r.attr3 || ''
    ]);
  });
  if (out.length) {
    var startRow = 2;
    var endRow = startRow + out.length - 1;
    sheet.getRange(startRow, 1, endRow, ROSTER_HEADERS.length).setValues(out);
  }
  return { saved: (rows || []).length, rosterName: rosterName };
}

function parseRosterTsv(tsvText) {
  var lines = String(tsvText || '').split(/\r?\n/).filter(function(l) { return l.trim(); });
  var rows = [];
  lines.forEach(function(line) {
    var parts = line.split('\t');
    if (parts.length === 1) parts = line.split(/[,;]/);
    rows.push(parts.map(function(p) { return String(p).trim(); }));
  });
  var colCount = 0;
  rows.forEach(function(r) { colCount = Math.max(colCount, r.length); });
  return { rows: rows, colCount: colCount, previewRows: rows.slice(0, 8) };
}

function importRosterWithMapping(rosterName, rawRows, columnMapping) {
  if (!rosterName || !String(rosterName).trim()) throw new Error('名簿名を指定してください。');
  rosterName = String(rosterName).trim();
  columnMapping = columnMapping || {};
  var parsed = [];
  (rawRows || []).forEach(function(parts, lineIdx) {
    if (!parts || !parts.length) return;
    var row = { studentId: '', year: '', classNo: '', number: '', name: '', attr1: '', attr2: '', attr3: '' };
    var hasData = false;
    Object.keys(columnMapping).forEach(function(colIdx) {
      var key = columnMapping[colIdx];
      if (!key || key === 'ignore') return;
      var val = parts[parseInt(colIdx, 10)];
      if (val == null || val === '') return;
      hasData = true;
      if (key === 'id') row.studentId = String(val);
      else if (key === 'year') row.year = String(val);
      else if (key === 'classNo') row.classNo = String(val);
      else if (key === 'number') row.number = String(val);
      else if (key === 'name') row.name = String(val);
      else if (key === 'attr1') row.attr1 = String(val);
      else if (key === 'attr2') row.attr2 = String(val);
      else if (key === 'attr3') row.attr3 = String(val);
    });
    if (!hasData) return;
    if (lineIdx === 0 && row.studentId && (row.studentId.indexOf('ID') >= 0 || row.studentId.indexOf('id') >= 0)) return;
    parsed.push(row);
  });
  if (!parsed.length) throw new Error('取込可能な行がありません。列マッピングを確認してください。');
  return saveRosterRows(rosterName, parsed);
}

function compareRosterRows_(a, b) {
  var ca = parseInt(a.classNo, 10);
  var cb = parseInt(b.classNo, 10);
  if (isNaN(ca)) ca = 0;
  if (isNaN(cb)) cb = 0;
  if (ca !== cb) return ca - cb;
  var na = parseInt(a.number, 10);
  var nb = parseInt(b.number, 10);
  if (isNaN(na)) na = 0;
  if (isNaN(nb)) nb = 0;
  if (na !== nb) return na - nb;
  return String(a.studentId).localeCompare(String(b.studentId), 'ja');
}

function getIdAssignmentStatus() {
  var ss = getActiveTestSs();
  var useOmr = getTestInfoValue(ss, 'IDマーク欄使用');
  if (useOmr === '') useOmr = 'true';
  var useOmrFlag = useOmr === 'true' || useOmr === '1' || useOmr === 'はい';
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  var total = 0;
  var withId = 0;
  if (sheet.getLastRow() > 1) {
    var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
    var colMap = getResultColumnMap(headers);
    var data = sheet.getRange(2, 1, sheet.getLastRow() - 1, sheet.getLastColumn()).getValues();
    data.forEach(function(row) {
      total++;
      var sid = colMap.studentId >= 0 ? String(row[colMap.studentId] || '').trim() : '';
      if (sid && sid.indexOf('?') < 0) withId++;
    });
  }
  var majorityHasId = total > 0 && withId > total / 2;
  return {
    useOmrIdMark: useOmrFlag,
    resultCount: total,
    withIdCount: withId,
    skipAssignment: useOmrFlag && majorityHasId,
    selectedRosterName: getTestInfoValue(ss, '選択名簿名')
  };
}

function saveSelectedRosterName(rosterName) {
  var ss = getActiveTestSs();
  setTestInfoValue(ss, '選択名簿名', rosterName || '');
  return { rosterName: rosterName || '' };
}

function saveIdMarkSetting(useOmr) {
  var ss = getActiveTestSs();
  setTestInfoValue(ss, 'IDマーク欄使用', useOmr ? 'true' : 'false');
  return { useOmr: !!useOmr };
}

function getRosterCsvForVerification(rosterName) {
  var rows = getRosterRows(rosterName || getTestInfoValue(getActiveTestSs(), '選択名簿名'));
  return rows.map(function(r) {
    return (r.studentId || '') + ',' + (r.name || '');
  }).join('\n');
}

function assignIdsFromRoster(rosterName, absentStudents) {
  var ss = getActiveTestSs();
  rosterName = rosterName || getTestInfoValue(ss, '選択名簿名');
  if (!rosterName) throw new Error('名簿を選択してください。');

  var status = getIdAssignmentStatus();
  if (status.skipAssignment) {
    return {
      skipped: true,
      reason: 'IDマーク欄使用かつ採点結果の過半数にIDが入力済みのため割当をスキップしました。',
      updated: 0,
      warnings: []
    };
  }

  var absentBuilt = buildAbsentRowIndexSet_(rosterName, absentStudents);
  var absentSet = absentBuilt.absentSet;
  var rosterAll = absentBuilt.rosterAll;
  var rosterSorted = rosterAll.filter(function(r) {
    return !absentSet[r.rowIndex];
  }).sort(compareRosterRows_);

  var folderId = getTestInfoValue(ss, '生徒解答フォルダID');
  if (!folderId) throw new Error('生徒解答フォルダIDが未設定です。Step③でフォルダを指定してください。');

  sortResultsSheetByFileNameAsc_(ss);
  var warpedInfo = getWarpedFileCountForRosterAssign_(ss);
  var filesSorted = sortByAssignFileNameAsc_(warpedInfo.files || []);

  if (filesSorted.length !== rosterSorted.length) {
    throw new Error(
      '補正済み画像 ' + filesSorted.length + ' 件（フォルダ直下）と受験予定 ' + rosterSorted.length +
      ' 名（名簿' + rosterAll.length + '−未受験' + (rosterAll.length - rosterSorted.length) + '）が一致しないため割当を中止しました。' +
      '未受験の☑を確認するか、フォルダ直下の補正済み画像（補正_*）の件数を確認してください。'
    );
  }
  if (!filesSorted.length) {
    throw new Error('フォルダ直下に補正済み画像（補正_*）がありません。Step③で画像補正を確認してください。');
  }

  var sheet = ss.getSheetByName(SHEET_RESULTS);
  var lookups = buildResultRowLookups_(sheet);
  var updated = 0;
  var warnings = [];

  for (var i = 0; i < filesSorted.length; i++) {
    var file = filesSorted[i];
    var student = rosterSorted[i];
    var rowIdx = findResultRowForWarpedFile_(file, lookups);
    if (rowIdx) {
      updateStudentIdentity(rowIdx, student.studentId, student.name);
      updated++;
    } else {
      var label = file.originalFileName || file.name;
      warnings.push('OCR未完了: ' + label + ' → ' + student.studentId + ' ' + student.name);
    }
  }

  saveRosterAbsentState_(ss, rosterName, absentBuilt.entries);
  setTestInfoValue(ss, '選択名簿名', rosterName);
  return {
    skipped: false,
    updated: updated,
    fileCount: filesSorted.length,
    rosterCount: rosterSorted.length,
    warnings: warnings
  };
}

function getRosterAssignmentPreview(rosterName, absentStudents) {
  var ss = getActiveTestSs();
  rosterName = rosterName || getTestInfoValue(ss, '選択名簿名');
  if (!rosterName) throw new Error('名簿を選択してください。');

  var absentBuilt = buildAbsentRowIndexSet_(rosterName, absentStudents);
  var absentSet = absentBuilt.absentSet;
  var rosterAll = absentBuilt.rosterAll;
  var rosterTotal = rosterAll.length;
  var absentCount = rosterAll.filter(function(r) { return absentSet[r.rowIndex]; }).length;
  var rosterActive = rosterTotal - absentCount;

  var warpedInfo = getWarpedFileCountForRosterAssign_(ss);
  var fileCount = warpedInfo.count || 0;

  var canAssign = rosterActive > 0 && fileCount > 0 && fileCount === rosterActive;
  var mismatch = '';
  if (fileCount !== rosterActive) {
    mismatch = '補正済み画像 ' + fileCount + ' 件（フォルダ直下）と受験予定 ' + rosterActive +
      ' 名（名簿' + rosterTotal + '−未受験' + absentCount + '）が一致しません。' +
      '未受験の☑を確認するか、フォルダ直下の補正済み画像（補正_*）の件数を確認してください。';
  }

  return {
    rosterName: rosterName,
    rosterTotal: rosterTotal,
    absentCount: absentCount,
    rosterActive: rosterActive,
    fileCount: fileCount,
    canAssign: canAssign,
    mismatch: mismatch
  };
}


// ========== IdentityService.gs ==========

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

function getWarpedFileIdFromResults(ss, sourceFileId) {
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (sheet.getLastRow() <= 1) return '';
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);
  if (colMap.fileId < 0 || colMap.warpedFileId < 0) return '';
  var data = sheet.getRange(2, 1, sheet.getLastRow() - 1, sheet.getLastColumn()).getValues();
  for (var i = 0; i < data.length; i++) {
    if (String(data[i][colMap.fileId]) === String(sourceFileId)) {
      return String(data[i][colMap.warpedFileId] || '');
    }
  }
  return '';
}

function cleanupWarpScriptProperties() {
  var props = PropertiesService.getScriptProperties();
  var all = props.getProperties();
  var removed = 0;
  Object.keys(all).forEach(function(key) {
    if (key.indexOf('WARP_') === 0) {
      props.deleteProperty(key);
      removed++;
    }
  });
  SpreadsheetApp.getUi().alert('WARP_* プロパティを ' + removed + ' 件削除しました。');
  return removed;
}

function getWarpedImageBase64(sourceFileId) {
  var ss = getActiveTestSs();
  var warpedId = getWarpedFileIdFromResults(ss, sourceFileId);
  if (warpedId) {
    try {
      return getDriveFileBase64(warpedId);
    } catch (e) { /* fallback */ }
  }
  var folder = getOrCreateTestImageFolder(ss);
  var files = folder.getFiles();
  while (files.hasNext()) {
    var f = files.next();
    if (f.getName().indexOf(String(sourceFileId)) >= 0 && f.getName().indexOf('補正_') === 0) {
      return getDriveFileBase64(f.getId());
    }
  }
  return getDriveFileBase64(sourceFileId);
}

// ========== ExportService.gs ==========

/**
 * 個票（判定・得点描画）出力
 */

function getAvailableOutputSlotKeys_(ss) {
  ss = ss || getActiveTestSs();
  var keys = [];
  getDomainColumnLabels(ss).forEach(function(l) {
    keys.push(String(l).replace(/_得点$/, ''));
  });
  keys.push('総計点');
  keys.push('外部連携得点');
  return keys;
}

function getResultColumnNameForSlotKey_(slotKey) {
  var k = String(slotKey || '').trim();
  if (!k) return '';
  if (k === '総計点' || k === '外部連携得点') return k;
  if (k.indexOf('_得点') >= 0) return k;
  return k + '_得点';
}

function getOutputSlots(ss) {
  ss = ss || getActiveTestSs();
  ensureOutputSlotsSheet(ss);
  var sheet = ss.getSheetByName(SHEET_OUTPUT_SLOTS);
  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return [];
  var slots = [];
  for (var i = 1; i < data.length; i++) {
    if (!data[i][0]) continue;
    slots.push({
      slotKey: String(data[i][0]),
      x: parseInt(data[i][1], 10) || 0,
      y: parseInt(data[i][2], 10) || 0,
      width: parseInt(data[i][3], 10) || 0,
      height: parseInt(data[i][4], 10) || 0,
      printMode: String(data[i][5] || 'number') === 'label' ? 'label' : 'number'
    });
  }
  return slots;
}

function saveOutputSlots(slots) {
  var ss = getActiveTestSs();
  ensureOutputSlotsSheet(ss);
  var sheet = ss.getSheetByName(SHEET_OUTPUT_SLOTS);
  sheet.clear();
  sheet.appendRow(['slotKey', 'x', 'y', 'width', 'height', 'printMode']);
  (slots || []).forEach(function(s) {
    if (!s || !s.slotKey) return;
    var w = parseInt(s.width, 10) || 0;
    var h = parseInt(s.height, 10) || 0;
    if (w <= 0 || h <= 0) return;
    sheet.appendRow([
      String(s.slotKey),
      parseInt(s.x, 10) || 0,
      parseInt(s.y, 10) || 0,
      w,
      h,
      String(s.printMode || 'number') === 'label' ? 'label' : 'number'
    ]);
  });
  return getOutputSlots(ss);
}

function buildFeedbackRowPayload_(ss, rowIndex) {
  ss = ss || getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (!sheet || rowIndex < 2 || rowIndex > sheet.getLastRow()) {
    throw new Error('無効な行番号です: ' + rowIndex);
  }
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);
  var row = sheet.getRange(rowIndex, 1, rowIndex, sheet.getLastColumn()).getValues()[0];
  var fields = getAnswerFields(ss);
  var fieldMarks = {};
  fields.forEach(function(f) {
    var label = f.displayName || f.id;
    var fm = colMap.fields[label];
    fieldMarks[f.id] = {
      judgment: fm && fm.judgment >= 0 ? String(row[fm.judgment] || '') : '',
      score: fm && fm.score >= 0 ? (parseInt(row[fm.score], 10) || 0) : 0
    };
  });
  var totals = {};
  getAvailableOutputSlotKeys_(ss).forEach(function(k) {
    var colName = getResultColumnNameForSlotKey_(k);
    var idx = headers.indexOf(colName);
    totals[k] = idx >= 0 ? row[idx] : '';
  });
  return {
    rowIndex: rowIndex,
    studentId: colMap.studentId >= 0 ? String(row[colMap.studentId] || '') : '',
    name: colMap.name >= 0 ? String(row[colMap.name] || '') : '',
    fileName: colMap.fileName >= 0 ? String(row[colMap.fileName] || '') : '',
    fileId: colMap.fileId >= 0 ? String(row[colMap.fileId] || '') : '',
    warpedFileId: colMap.warpedFileId >= 0 ? String(row[colMap.warpedFileId] || '') : '',
    fieldMarks: fieldMarks,
    totals: totals
  };
}

function getFeedbackRowPayload(rowIndex) {
  return buildFeedbackRowPayload_(getActiveTestSs(), rowIndex);
}

function getFeedbackExportConfig() {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  var rows = [];
  if (sheet && sheet.getLastRow() > 1) {
    var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
    var colMap = getResultColumnMap(headers);
    var data = sheet.getRange(2, 1, sheet.getLastRow(), sheet.getLastColumn()).getValues();
    for (var i = 0; i < data.length; i++) {
      rows.push({
        rowIndex: i + 2,
        studentId: colMap.studentId >= 0 ? String(data[i][colMap.studentId] || '') : '',
        name: colMap.name >= 0 ? String(data[i][colMap.name] || '') : '',
        fileName: colMap.fileName >= 0 ? String(data[i][colMap.fileName] || '') : '',
        fileId: colMap.fileId >= 0 ? String(data[i][colMap.fileId] || '') : '',
        warpedFileId: colMap.warpedFileId >= 0 ? String(data[i][colMap.warpedFileId] || '') : ''
      });
    }
  }
  var feedbackFolder = getOrCreateFeedbackFolder_(ss);
  return {
    fields: getAnswerFields(ss),
    outputSlots: getOutputSlots(ss),
    availableSlotKeys: getAvailableOutputSlotKeys_(ss),
    rows: rows,
    feedbackFolderUrl: feedbackFolder.getUrl()
  };
}

function saveFeedbackImage(rowIndex, base64Image) {
  var ss = getActiveTestSs();
  var payload = buildFeedbackRowPayload_(ss, rowIndex);
  if (!payload.fileId && !payload.warpedFileId) {
    throw new Error('補正画像がありません: ' + (payload.fileName || payload.studentId || rowIndex));
  }
  var folder = getOrCreateFeedbackFolder_(ss);
  var studentId = payload.studentId || 'unknown';
  var safeName = (payload.fileName || 'image').replace(/[^\w\u3040-\u30ff\u4e00-\u9faf.\-]/g, '_').substring(0, 80);
  var fileName = '個票_' + studentId + '_' + safeName + '.jpg';
  var b64 = String(base64Image || '');
  if (b64.indexOf(',') >= 0) b64 = b64.split(',')[1];
  var bytes = Utilities.base64Decode(b64);
  var existing = folder.getFilesByName(fileName);
  while (existing.hasNext()) {
    existing.next().setTrashed(true);
  }
  var file = folder.createFile(Utilities.newBlob(bytes, 'image/jpeg', fileName));
  return {
    fileId: file.getId(),
    fileName: fileName,
    folderUrl: folder.getUrl(),
    rowIndex: rowIndex
  };
}

function markFeedbackExportComplete() {
  var ss = getActiveTestSs();
  touchTestProgress_(ss, 10);
  return { feedbackFolderUrl: getOrCreateFeedbackFolder_(ss).getUrl() };
}
